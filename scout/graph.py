from pydantic import BaseModel, Field
from typing import Annotated, List, Generator

from langchain_google_genai import ChatGoogleGenerativeAI

from langchain_core.messages import (
    BaseMessage,
    HumanMessage,
    SystemMessage,
    AIMessageChunk,
)

from langgraph.graph.message import add_messages
from langgraph.graph import StateGraph, START, END
from langgraph.prebuilt import ToolNode
from langgraph.checkpoint.memory import InMemorySaver

from scout.tools import query_db, generate_visualization, fetch_nasa_nicer_products, execute_nicer_analysis
from scout.prompts import prompts


class ScoutState(BaseModel):
    messages: Annotated[List[BaseMessage], add_messages] = Field(
        default_factory=list
    )
    chart_json: str = ""


class Agent:
    """
    Agent class for implementing LangGraph agents.
    """

    def __init__(
        self,
        name: str,
        tools: List = [query_db, generate_visualization,fetch_nasa_nicer_products, execute_nicer_analysis],
        model: str = "gemini-2.5-flash",
        system_prompt: str = "You are a helpful assistant.",
        temperature: float = 0.1,
    ):

        self.name = name
        self.tools = tools
        self.model = model
        self.system_prompt = system_prompt
        self.temperature = temperature

        # Gemini LLM
        self.llm = ChatGoogleGenerativeAI(
            model=self.model,
            temperature=self.temperature,
        ).bind_tools(self.tools)

        self.runnable = self.build_graph()

    def build_graph(self):
        """
        Build the LangGraph application.
        """

        # def scout_node(state: ScoutState) -> ScoutState:

        #     response = self.llm.invoke(
        #         [SystemMessage(content=self.system_prompt)]
        #         + state.messages
        #     )

        #     state.messages.append(response)

        #     return state

        def scout_node(state: ScoutState) -> dict:
            response = self.llm.invoke(
                [SystemMessage(content=self.system_prompt)] + state.messages
            )
        
            return {"messages": [response]}
        

        def router(state: ScoutState) -> str:

            last_message = state.messages[-1]

            # Gemini tool calls
            if not getattr(last_message, "tool_calls", None):
                return END

            return "tools"

        builder = StateGraph(ScoutState)

        builder.add_node("chatbot", scout_node)
        builder.add_node("tools", ToolNode(self.tools))

        builder.add_edge(START, "chatbot")

        builder.add_conditional_edges(
            "chatbot",
            router,
            ["tools", END],
        )

        builder.add_edge("tools", "chatbot")

        memory = InMemorySaver()

        return builder.compile(checkpointer=memory)

    def inspect_graph(self):
        """
        Visualize the graph.
        """

        from IPython.display import display, Image

        graph = self.build_graph()

        display(
            Image(
                graph.get_graph(xray=True).draw_mermaid_png()
            )
        )

    def invoke(
        self,
        message: str,
        thread_id: str = "default-thread",
        **kwargs
    ) -> str:
        """
        Synchronously invoke the graph.
        """

        result = self.runnable.invoke(
            input={
                "messages": [HumanMessage(content=message)]
            },
            config={
                "configurable": {
                    "thread_id": thread_id
                }
            },
            **kwargs
        )

        return result["messages"][-1].content

    def stream(
        self,
        message: str,
        thread_id: str = "default-thread",
        **kwargs
    ) -> Generator[str, None, None]:
        """
        Stream graph results.
        """

        for message_chunk, metadata in self.runnable.stream(
            input={
                "messages": [HumanMessage(content=message)]
            },
            config={
                "configurable": {
                    "thread_id": thread_id
                }
            },
            stream_mode="messages",
            **kwargs
        ):

            if isinstance(message_chunk, AIMessageChunk):

                # Gemini tool calls
                if getattr(message_chunk, "tool_call_chunks", None):

                    tool_chunk = message_chunk.tool_call_chunks[0]

                    tool_name = tool_chunk.get("name", "")
                    args = tool_chunk.get("args", "")

                    if tool_name:
                        yield f"\n\n< TOOL CALL: {tool_name} >\n\n"

                    if args:
                        yield args

                else:

                    if message_chunk.content:
                        yield message_chunk.content


# Instantiate agent
agent = Agent(
    name="Scout",
    system_prompt=prompts.scout_system_prompt,
)

graph = agent.build_graph()
