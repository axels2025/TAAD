"""
LangGraph Trading Agent
Orchestrates the trading workflow using LangGraph state machine
"""
import logging
import os
from typing import TypedDict, List, Dict, Annotated
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
from langgraph.graph import StateGraph, END, add_messages
from langgraph.prebuilt import ToolNode
from langchain_core.tools import tool
from dotenv import load_dotenv

from tools.uptrend_screener import screen_uptrend_stocks, get_sp500_symbols
from tools.options_finder import find_options_for_stocks
from tools.margin_calculator import (
    rank_opportunities_by_margin,
    get_portfolio_margin_capacity,
    calculate_margin_for_options
)
from config import trading_config as cfg

load_dotenv()
logger = logging.getLogger(__name__)

# Module-level variables to store workflow configuration
_workflow_symbols = None
_workflow_trend_filter = None


class TradingState(TypedDict):
    """State for trading workflow"""
    messages: Annotated[List[HumanMessage | AIMessage | SystemMessage], add_messages]
    symbols: List[str]
    uptrend_stocks: List[Dict]
    options_opportunities: Dict[str, List[Dict]]
    margin_info: Dict
    ranked_opportunities: List[Dict]
    top_recommendation: Dict
    error: str


# Define tools for the agent
@tool
def screen_stocks_tool(num_stocks: int = None) -> str:
    """
    Screen stocks with trend detection and filtering

    Detects and filters by trend:
    - Uptrend: Price > SMA20 > SMA50
    - Downtrend: Price < SMA20 < SMA50
    - Sideways: SMA20 and SMA50 within 2%
    - All: No trend filtering

    Args:
        num_stocks: Number of stocks to screen (default from config)
    """
    try:
        global _workflow_symbols, _workflow_trend_filter

        if num_stocks is None:
            num_stocks = cfg.NUM_STOCKS_TO_SCREEN

        # Use workflow symbols if available, otherwise use S&P 500
        if _workflow_symbols:
            symbols = _workflow_symbols[:num_stocks]
        else:
            symbols = get_sp500_symbols()[:num_stocks]

        # Use workflow trend filter if available
        trend_filter = _workflow_trend_filter if _workflow_trend_filter else cfg.TREND_FILTER

        logger.info(f"Screening {len(symbols)} stocks with trend filter: {trend_filter}")
        results = screen_uptrend_stocks(symbols, trend_filter=trend_filter)

        if not results:
            trend_desc = ', '.join(trend_filter) if 'all' not in trend_filter else 'any trend'
            return (
                f"No stocks found matching trend filter ({trend_desc}) out of {len(symbols)} screened.\n"
                f"Consider: 1) Trying a different stock index, 2) Adjusting the trend filter, "
                f"or 3) Using --no-trend to see all stocks regardless of trend pattern."
            )

        # Group results by trend
        by_trend = {}
        for stock in results:
            trend = stock['trend']
            if trend not in by_trend:
                by_trend[trend] = []
            by_trend[trend].append(stock)

        # Build summary
        trend_filter_desc = ', '.join(trend_filter) if 'all' not in trend_filter else 'all trends'
        summary = f"Found {len(results)} stocks matching filter ({trend_filter_desc}):\n"

        for trend_type in ['uptrend', 'downtrend', 'sideways', 'unclear']:
            if trend_type in by_trend:
                trend_emoji = {'uptrend': '↗', 'downtrend': '↘', 'sideways': '→', 'unclear': '?'}[trend_type]
                summary += f"\n{trend_emoji} {trend_type.upper()} ({len(by_trend[trend_type])}):\n"
                for stock in by_trend[trend_type]:
                    summary += (
                        f"  {stock['symbol']}: ${stock['price']:.2f} "
                        f"(SMA20: ${stock['sma_20']:.2f}, SMA50: ${stock['sma_50']:.2f}, "
                        f"Strength: {stock['trend_strength']:+.1f}%)\n"
                    )

        return summary

    except Exception as e:
        logger.error(f"Error in screen_stocks_tool: {e}")
        return f"Error screening stocks: {str(e)}"


@tool
def find_options_tool(symbol: str, stock_price: float) -> str:
    """
    Find PUT options based on configured criteria

    Args:
        symbol: Stock symbol to find options for
        stock_price: Current stock price
    """
    try:
        from tools.options_finder import find_put_options

        logger.info(f"Finding PUT options for {symbol}")

        # Parameters from config/trading_config.py
        options = find_put_options(
            symbol=symbol,
            current_price=stock_price,
            otm_range=(cfg.OTM_MIN, cfg.OTM_MAX),
            premium_range=(cfg.PREMIUM_MIN, cfg.PREMIUM_MAX),
            min_dte=cfg.DTE_MIN,
            max_dte=cfg.DTE_MAX
        )

        if not options:
            return f"No matching PUT options found for {symbol}"

        summary = f"Found {len(options)} PUT options for {symbol}:\n"
        for opt in options[:cfg.TOP_OPPORTUNITIES]:
            summary += (
                f"\n${opt['strike']} PUT exp {opt['expiration']} "
                f"@ ${opt['premium']:.2f} ({opt['otm_percentage']:.1f}% OTM, {opt['dte']} DTE)"
            )

        return summary

    except Exception as e:
        logger.error(f"Error in find_options_tool: {e}")
        return f"Error finding options for {symbol}: {str(e)}"


@tool
def calculate_margin_tool(
    stock_price: float,
    strike: float,
    premium: float,
    contracts: int = None
) -> str:
    """
    Calculate margin requirement and efficiency for PUT option trade

    Args:
        stock_price: Current stock price
        strike: Option strike price
        premium: Option premium per share
        contracts: Number of contracts (default from config)
    """
    try:
        from tools.margin_calculator import calculate_put_margin

        if contracts is None:
            contracts = cfg.CONTRACTS_PER_TRADE

        result = calculate_put_margin(stock_price, strike, premium, contracts)

        summary = (
            f"Margin Analysis for {contracts} contracts:\n"
            f"  Premium Received: ${result['total_premium_received']:.2f}\n"
            f"  Margin Required: ${result['margin_required']:.2f}\n"
            f"  Return on Margin: {result['return_on_margin']:.2f}%\n"
            f"  OTM Percentage: {result['otm_percentage']:.2f}%"
        )

        return summary

    except Exception as e:
        logger.error(f"Error in calculate_margin_tool: {e}")
        return f"Error calculating margin: {str(e)}"


@tool
def get_account_margin_tool() -> str:
    """Get available margin from IBKR account"""
    try:
        margin_info = get_portfolio_margin_capacity()

        if 'error' in margin_info:
            return f"Error getting margin: {margin_info['error']}"

        summary = (
            f"Account Margin Information:\n"
            f"  Net Liquidation: ${margin_info['net_liquidation']:,.2f}\n"
            f"  Buying Power: ${margin_info['buying_power']:,.2f}\n"
            f"  Available Funds: ${margin_info['available_funds']:,.2f}\n"
            f"  Excess Liquidity: ${margin_info['excess_liquidity']:,.2f}"
        )

        return summary

    except Exception as e:
        logger.error(f"Error in get_account_margin_tool: {e}")
        return f"Error getting account margin: {str(e)}"


# Create tools list
tools = [
    screen_stocks_tool,
    find_options_tool,
    calculate_margin_tool,
    get_account_margin_tool
]


def create_trading_agent():
    """Create the LangGraph trading agent"""

    # Initialize LLM with tools
    llm = ChatAnthropic(
        model="claude-sonnet-4-20250514",
        api_key=os.getenv("ANTHROPIC_API_KEY"),
        temperature=0
    )
    llm_with_tools = llm.bind_tools(tools)

    # Define system message (dynamically includes current config and trend filter)
    option_type = "weekly" if cfg.DTE_MAX <= 14 else "monthly"
    trend_filter_desc = ', '.join(_workflow_trend_filter) if _workflow_trend_filter else 'uptrend'

    system_message = SystemMessage(content=f"""You are an intelligent trading assistant that helps find profitable PUT option selling opportunities.

Your workflow:
1. Screen stocks with trend detection (filtering for: {trend_filter_desc})
   - Uptrend: Price > SMA{cfg.SMA_SHORT} > SMA{cfg.SMA_LONG}
   - Downtrend: Price < SMA{cfg.SMA_SHORT} < SMA{cfg.SMA_LONG}
   - Sideways: SMA{cfg.SMA_SHORT} and SMA{cfg.SMA_LONG} within 2%
2. For matching stocks, find PUT options {cfg.OTM_MIN*100:.0f}-{cfg.OTM_MAX*100:.0f}% OTM with ${cfg.PREMIUM_MIN:.2f}-${cfg.PREMIUM_MAX:.2f} premium ({option_type} options, {cfg.DTE_MIN}-{cfg.DTE_MAX} DTE)
3. Calculate margin requirements for {cfg.CONTRACTS_PER_TRADE}-contract trades
4. Rank opportunities by margin efficiency (return on margin)
5. Present top {cfg.TOP_OPPORTUNITIES} recommendations with analysis

IMPORTANT RULES:
- If screening returns NO stocks matching the trend filter, inform the user and stop. Do NOT keep re-screening.
- If stocks are found but NO options match the criteria, clearly explain this and provide actionable suggestions.
- Only screen stocks ONCE per workflow execution.
- When providing suggestions, be specific about which config parameters to adjust (OTM_MIN/MAX, PREMIUM_MIN/MAX, DTE_MIN/MAX).
- Always provide a clear final summary, even when no opportunities are found.

Always use the tools available to you. Be systematic but efficient in your analysis.
""")

    # Define agent node
    def call_model(state: TradingState):
        messages = state.get("messages", [])

        # Prepend system message if not present
        if not messages or not isinstance(messages[0], SystemMessage):
            messages = [system_message] + messages

        logger.debug(f"Agent calling LLM with {len(messages)} messages")
        response = llm_with_tools.invoke(messages)

        # Log what the agent is doing
        has_tool_calls = hasattr(response, "tool_calls") and response.tool_calls
        if has_tool_calls:
            tool_names = [tc.get("name") for tc in response.tool_calls]
            logger.info(f"Agent requesting tools: {tool_names}")
        else:
            logger.info("Agent providing final response (no tool calls)")

        return {"messages": [response]}

    # Define tool execution node
    tool_node = ToolNode(tools)

    # Routing function
    def should_continue(state: TradingState):
        messages = state["messages"]
        last_message = messages[-1]

        # If LLM makes a tool call, continue to tools
        if hasattr(last_message, "tool_calls") and last_message.tool_calls:
            logger.debug("Routing: continuing to tools")
            return "tools"
        # Otherwise end
        logger.info("Routing: ending workflow (no more tool calls)")
        return END

    # Build graph
    workflow = StateGraph(TradingState)

    # Add nodes
    workflow.add_node("agent", call_model)
    workflow.add_node("tools", tool_node)

    # Set entry point
    workflow.set_entry_point("agent")

    # Add edges
    workflow.add_conditional_edges(
        "agent",
        should_continue,
        {
            "tools": "tools",
            END: END
        }
    )

    # Tools always go back to agent
    workflow.add_edge("tools", "agent")

    # Compile graph
    app = workflow.compile()

    logger.info("Trading agent graph compiled successfully")
    return app


def run_trading_workflow(
    num_stocks: int = 25,
    symbols: List[str] = None,
    custom_prompt: str = None,
    trend_filter: List[str] = None
) -> Dict:
    """
    Run the full trading workflow

    Args:
        num_stocks: Number of stocks to screen
        symbols: List of stock symbols to screen (if None, uses S&P 500)
        custom_prompt: Optional custom prompt for the agent
        trend_filter: List of trends to filter by (e.g., ['uptrend', 'sideways'])
                     Use ['all'] for no filtering (default from config)

    Returns:
        Dict with workflow results
    """
    logger.info("Starting trading workflow")

    # Store symbols and trend filter in module-level variables for tools to access
    global _workflow_symbols, _workflow_trend_filter
    if symbols:
        _workflow_symbols = symbols[:num_stocks]
    else:
        _workflow_symbols = get_sp500_symbols()[:num_stocks]

    _workflow_trend_filter = trend_filter if trend_filter else cfg.TREND_FILTER
    logger.info(f"Trend filter: {_workflow_trend_filter}")

    try:
        app = create_trading_agent()

        # Create initial prompt
        if custom_prompt:
            prompt = custom_prompt
        else:
            trend_filter_desc = ', '.join(_workflow_trend_filter) if _workflow_trend_filter else 'uptrend'
            prompt = f"""Please execute the full trading workflow:

1. Screen the top {num_stocks} stocks with trend filter: {trend_filter_desc}
2. If NO stocks match the trend filter, inform me and STOP. Do not continue to the next steps.
3. If matching stocks ARE found, find suitable PUT options ({cfg.OTM_MIN*100:.0f}-{cfg.OTM_MAX*100:.0f}% OTM, ${cfg.PREMIUM_MIN:.2f}-${cfg.PREMIUM_MAX:.2f} premium)
4. Get account margin information
5. Calculate margin for each option opportunity ({cfg.CONTRACTS_PER_TRADE} contracts)
6. Provide your top {cfg.TOP_OPPORTUNITIES} recommendations ranked by margin efficiency

IMPORTANT: Screen stocks only ONCE. If screening finds no matching stocks, provide suggestions and end the workflow immediately."""

        # Run workflow
        initial_state = {
            "messages": [HumanMessage(content=prompt)]
        }

        logger.info("Invoking agent workflow...")
        print()  # Blank line for readability
        from utils_progress import StatusPrinter
        StatusPrinter.info("Agent is analyzing results and preparing recommendations...")
        print()  # Another blank line

        import time
        start_time = time.time()

        # Add recursion limit to prevent infinite loops
        # This limits the number of agent->tool->agent cycles
        config = {"recursion_limit": 50}

        try:
            final_state = app.invoke(initial_state, config)
        except Exception as e:
            elapsed = time.time() - start_time
            logger.error(f"Agent workflow failed after {elapsed:.1f}s: {e}")
            raise

        elapsed = time.time() - start_time
        logger.info(f"Agent workflow completed in {elapsed:.1f}s")

        # Extract final recommendation
        messages = final_state.get("messages", [])
        final_response = messages[-1].content if messages else "No response generated"

        logger.info(f"Trading workflow completed successfully ({len(messages)} total messages)")

        # Check if response is meaningful
        if not final_response or len(final_response.strip()) < 50:
            logger.warning(f"Agent provided very short response: '{final_response[:100]}'")
            final_response = (
                "Workflow completed but agent provided minimal feedback. "
                "This may indicate no trading opportunities were found. "
                "Check the logs above for screening and options search results."
            )

        # Log if the workflow found any stocks
        has_no_stocks = any("No stocks" in str(msg.content)
                           for msg in messages
                           if hasattr(msg, 'content'))
        has_no_options = any("No options" in str(msg.content) or "No matching" in str(msg.content)
                            for msg in messages
                            if hasattr(msg, 'content'))

        if has_no_stocks:
            logger.info("Workflow completed with no stocks matching trend filter - normal exit")
        elif has_no_options:
            logger.info("Workflow completed with stocks found but no options matched criteria - normal exit")

        return {
            "success": True,
            "messages": messages,
            "final_recommendation": final_response
        }

    except Exception as e:
        logger.error(f"Error in trading workflow: {e}", exc_info=True)
        return {
            "success": False,
            "error": str(e)
        }


if __name__ == "__main__":
    # Test the agent
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    result = run_trading_workflow(num_stocks=10)

    if result["success"]:
        print("\n" + "="*80)
        print("TRADING WORKFLOW RESULT")
        print("="*80)
        print(result["final_recommendation"])
    else:
        print(f"\nError: {result['error']}")
