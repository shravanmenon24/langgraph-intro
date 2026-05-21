"""
Tools for the agent to use.
"""
from langchain_core.tools import tool
from sqlalchemy import create_engine, text, Engine
import pandas as pd
from scout import env
from langgraph.types import Command
from langchain_core.tools.base import InjectedToolCallId
from typing import Annotated
from langchain_core.messages import ToolMessage
import subprocess
import sys
import os
from astroquery.heasarc import Heasarc
from astropy.io import fits
import pandas as pd
import json


class ServerSession:
    """A session for server-side state management and operations. 
    
    In practice, this would be a separate service from where the agent is running and the agent would communicate with it using a REST API. In this simplified example, we use it to persist the db engine and data returned from the query_db tool.
    """
    def __init__(self):
        self.engine: Engine = None
        self.df: pd.DataFrame = None

        self.engine = self._get_engine()

    def _get_engine(self):
        if self.engine is None:
            # Configure SQLAlchemy for session pooling
            _engine = create_engine(
                env.SUPABASE_URL,
                pool_size=5,                # Smaller pool size since the pooler manages connections
                max_overflow=5,             # Fewer overflow connections needed
                pool_timeout=10,            # Shorter timeout for getting connections
                pool_recycle=1800,          # Recycle connections more frequently
                pool_pre_ping=True,         # Keep this to verify connections
                pool_use_lifo=True,         # Keep LIFO to reduce number of open connections
                connect_args={
                    "application_name": "onlyvans_agent",
                    "options": "-c statement_timeout=30000",
                    # Keepalives less important with transaction pooler but still good practice
                    "keepalives": 1,
                    "keepalives_idle": 60,
                    "keepalives_interval": 30,
                    "keepalives_count": 3
                }
            )
            return _engine
        return self.engine


# Create a global instance of the ServerSession
session = ServerSession()


@tool
def query_db(query: str) -> str:
    """Query the database using Postgres SQL.

    Args:
        query: The SQL query to execute. Must be a valid postgres SQL string that can be executed directly.

    Returns:
        str: The query result as a markdown table.
    """
    try:
        # Use the global engine in the server session to connect to Supabase
        with session.engine.connect().execution_options(
            isolation_level="READ COMMITTED"
        ) as conn:
            result = conn.execute(text(query))

            columns = list(result.keys())
            rows = result.fetchall()
            df = pd.DataFrame(rows, columns=columns)

            # Store the DataFrame in the server session
            session.df = df

            conn.close()  # Explicitly close the connection
        return df.to_markdown(index=False)
    except Exception as e:
        return f"Error executing query: {str(e)}"


@tool
def generate_visualization(
    name: str, 
    sql_query: str, 
    plotly_code: str,
    tool_call_id: Annotated[str, InjectedToolCallId]
    ) -> str:
    '''Generate a visualization using Python, SQL, and Plotly. If the visualizaton is successfully generated, it's automatically rendered for the user on the frontend.

    Args:
        name: The name of the visualization. Should be a short name with underscores and no spaces.
        sql_query: The SQL query to retrieve data for the visualization. Must be a valid postgres SQL string that can be executed directly. The query will be executed and the result will be loaded into a DataFrame named 'df'.
        plotly_code: Python code that generates a Plotly figure. The code should create a variable named 'fig' that contains the Plotly figure object.

    Returns:
        str: Success message if successful or an error message.

    ## Assumptions
    Assume the data is already loaded into a DataFrame named 'df' and the following libraries are already imported for immediate use: 
    
    import pandas as pd
    import plotly.express as px
    import plotly.graph_objects as go
    import plotly

    ## Example:
    User asks "Show me the top 5 creators by revenue"

    sql_query = "SELECT c.id, c.first_name, c.last_name, SUM(t.amount_usd) AS total_revenue\nFROM creators c\nJOIN transactions t ON c.id = t.creator_id\nGROUP BY c.id, c.first_name, c.last_name\nORDER BY total_revenue DESC\nLIMIT 5;"
    plotly_code = "fig = px.bar(df, x='first_name', y='total_revenue', title='Top 5 Creators by Revenue')\nfig.update_layout(xaxis_title='Creator', yaxis_title='Total Revenue ($)')"
    '''
    import io
    import os
    from contextlib import redirect_stdout, redirect_stderr

    # Create the output directory if it doesn't exist
    os.makedirs("output", exist_ok=True)

    # Set the output file path
    file_path = f"output/{name}.json"

    # Capture stdout and stderr
    stdout_capture = io.StringIO()
    stderr_capture = io.StringIO()

    # Add SQL query to the code

    pre_code = f'''
from sqlalchemy import text
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import plotly.io as pio
import plotly

# Generated SQL
df = pd.read_sql(text("""{sql_query}"""), engine)

# Generated plotly code
'''
    post_code = f'''

# Save the figure to JSON
if 'fig' in locals() or 'fig' in globals():
    fig_json = pio.to_json(fig)
    with open('{file_path}', 'w') as f:
        f.write(fig_json)
'''
    
    # Sandwich the plotly code like this to avoid indent errors from f-string
    code = pre_code + plotly_code + post_code

    # Prepare execution environment with database connection
    # exec_globals = {}

    # # Pass the server session engine to the code
    # if "engine" in code:
    #     exec_globals['engine'] = session.engine

    # try:
    #     # Execute the code with captured output
    #     print(f"Executing code: \n\n{code}\n\n")
    #     with redirect_stdout(stdout_capture), redirect_stderr(stderr_capture):
    #         exec(code, exec_globals, {})

    # Prepare execution environment with a unified context dictionary
    exec_context = {
        "engine": session.engine
    }

    try:
        # Execute the code with captured output
        print(f"Executing code: \n\n{code}\n\n")
        with redirect_stdout(stdout_capture), redirect_stderr(stderr_capture):
            # CRITICAL FIX: Pass exec_context as BOTH globals and locals
            exec(code, exec_context, exec_context)

        # Get the output and error messages
        print(f"STDOUT: \n\n{stdout_capture.getvalue()}\n")
        print(f"STDERR: \n\n{stderr_capture.getvalue()}\n")

        # Check if the fig was created
        if os.path.exists(file_path):
            with open(file_path, "r") as f:
                fig_json = f.read()
            return Command(
                update={
                    # update the state keys
                    "chart_json": fig_json,
                    # update the message history
                    "messages": [
                        ToolMessage(
                            "Visualization created successfully.", 
                            tool_call_id=tool_call_id
                        )
                    ],
                }
            )
        else:
            raise Exception(f"Error: Failed to generate visualization.\n\n<stderr>\n{stderr_capture.getvalue()}\n</stderr>")

    except Exception as e:
        # Get the error message
        error_message = str(e)
        return f"Error executing visualization code: {error_message}"

@tool
def fetch_nasa_nicer_products(target_name: str, obs_id: str = None, max_records: int = 3) -> str:
    """
    Queries NASA's live HEASARC archive for NICER observations.
    
    Args:
        target_name: The name of the celestial target (e.g., 'Cyg X-1'). REQUIRED.
        obs_id: A specific 10-digit ObsID string (e.g., '8656010601') to isolate one observation. Optional.
        max_records: Maximum records to download if no specific obs_id is provided.
    """
    try:
        heasarc = Heasarc()
        os.makedirs("nicer_data", exist_ok=True)
        
        print(f"Searching NASA HEASARC for target: {target_name}...")
        # A standard region query using the target name avoids the API crash
        table = heasarc.query_region(target_name, catalog="nicermastr")
        
        if table is None or len(table) == 0:
            return f"No observations found for target: {target_name}"
        
        # If an exact ObsID is requested, filter the table safely
        if obs_id:
            print(f"Filtering results for exact ObsID: {obs_id}...")
            
            # Extract OBSID column and strip out Astropy's byte-string formatting (b'...')
            table_obsids = [str(val).replace("b'", "").replace("'", "").strip() for val in table['OBSID']]
            
            if obs_id not in table_obsids:
                return f"ObsID {obs_id} not found in the recent data for {target_name}."
            
            # Slice the table to keep ONLY the matched row
            match_index = table_obsids.index(obs_id)
            table = table[match_index : match_index + 1]
            
        else:
            table = table[:max_records]
            
        print(f"Locating downloadable files for {len(table)} matching observation row(s)...")
        links = heasarc.locate_data(table)
        
        if links is None or len(links) == 0:
            return "No public high-level data products are available for this specific query."

        print("Downloading science products from HEASARC...")
        local_files = heasarc.download_data(links, location="nicer_data")
        
        return json.dumps({
            "status": "Success",
            "message": f"Successfully pulled data products.",
            "downloaded_files_count": len(local_files) if local_files else 0,
            "saved_location": "nicer_data/"
        }, indent=2)
        
    except Exception as e:
        return f"Failed to retrieve data from NASA web server: {str(e)}"

@tool
def execute_nicer_analysis(script_name: str, python_code: str) -> str:
    """
    Executes an advanced Python script for pulling, reprocessing, or analyzing 
    NICER data using libraries like heasoftpy, astroquery, and astropy.
    The code runs inside a local shell environment where HEASoft is initialized.
    """
    os.makedirs("nicer_workspace", exist_ok=True)
    file_path = f"nicer_workspace/{script_name}.py"
    
    # Write the AI-generated code string to a physical file
    with open(file_path, "w") as f:
        f.write(python_code)
        
    try:
        # Run it via a subprocess shell so it has access to heasoftpy / HEADAS
        result = subprocess.run(
            [sys.executable, file_path],
            capture_output=True,
            text=True,
            timeout=300 # Give it 5 minutes for heavy processing
        )
        
        output = result.stdout
        if result.stderr:
            output += f"\n--- RUNTIME ERRORS/WARNINGS ---\n{result.stderr}"
        return output if output else "Script executed successfully with no terminal output."
        
    except subprocess.TimeoutExpired:
        return "Error: Execution timed out. The data reduction took longer than 5 minutes."
