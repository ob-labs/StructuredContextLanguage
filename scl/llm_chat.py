import json
import logging
from scl.otel.otel import tracer
from scl.cap_reg import CapRegistry
from scl.meta.msg import Msg
from scl.config import config
from scl.otel.otel import cap_counts

## why not we just prvide the metrics and leave the function to user themself?
## using hooks to provide user capbility to overwrite the default behavior
## hence we just provides metrics as evdience to support user.
## we can further provide an prompt to user to generate the hook as most LLM can generate the code.
### to achive this
#### otel metric
#### using otel metric into hook
#### cache system
#### using cache value into hook
@tracer.start_as_current_span("send_messages")
def send_messages(
        client, model, 
        cap_registry:CapRegistry, 
        ToolNames, # todo here, support both name and give Cap
        msg:Msg, 
        Turns):
    if Turns == 0: 
        ## to do an autonomy sider
        ### hook of overwrite limit
        limit = config.limit
        ### hook of overwrite min_similarity
        min_similarity = config.min_similarity
        tools_named = cap_registry.getCapsByNames(ToolNames)
        ## metrics 
        ### search time,search number
        ### a key-value cache for information
        tools_autonomy = cap_registry.getCapsBySimilarity(msg, limit, min_similarity)
        ## metrics 
        ### search time,search number
        ### a key-value cache for information
        tools_history = cap_registry.getCapsByHistory(msg, limit, min_similarity)
        ## metrics 
        ### search time,search number
        ### a key-value cache for information

        ### to do an autonomy sider hook?
        tools_merged = {    
            **({} if tools_named is None else tools_named),
            **({} if tools_autonomy is None else tools_autonomy),
            **({} if tools_history is None else tools_history)
        }
        cap_counts["total"] = len(tools_merged)
        cap_counts["duplicate"] = len(tools_named) + len(tools_autonomy) + len(tools_history) - cap_counts["total"]
        ## metrics tool number,metrics as duplicate number? 
        ## or a cache for duplicate info
        tools = []
        for tool in list(tools_merged.values()):
            if tool.type != "skill":
                tools.append(tool.llm_description)
        ## todo-> debug/trace
        logging.info(tools)
        logging.info(msg)
        # Build request parameters - only include tools if not empty
        # Some APIs (like DashScope) don't accept empty tools list
        request_params = {
            "model": model,
            "messages": msg.messages
        }
        if tools:  # Only add tools parameter if tools list is not empty
            request_params["tools"] = tools
        
        response = client.chat.completions.create(**request_params)
        return response.choices[0].message
    else:
        response = client.chat.completions.create(
            model=model,
            messages=msg.messages
                )
        return response.choices[0].message

@tracer.start_as_current_span("function_call_playground")
def function_call_playground(
    client, model, 
    cap_registry:CapRegistry,
    ToolNames,
    msg:Msg,
    ): 
    turns = 0
    ## metric execution time
    response = send_messages(client, model, cap_registry, ToolNames, msg, turns)
    # todo, feedback loop model?(ref langchain)
    turns += 1
    ## todo-> debug/trace
    logging.info(response)
    if response.tool_calls:
        cap_counts["hit"] = len(response.tool_calls)
        for tool_call in response.tool_calls:
            ## metric accuery for each search? from LLM, back to cap_reg's cache
            func1_name = tool_call.function.name
            func1_args = tool_call.function.arguments
            ## todo-> debug/trace
            logging.info(f"func1_name: {func1_name}, func1_args: {func1_args}")
            args_dict = json.loads(func1_args)
            cap = cap_registry.get_cap_by_name(func1_name)
            func1_out = cap_registry.call_cap_safe(cap,args_dict)
            ## metric execution time
            cap_registry.record(msg, cap)

            msg.append(response)
            msg.append_cap_result(func1_out, tool_call.id)
        ## metric execution time
        response = send_messages(client, model, cap_registry, ToolNames, msg, turns)
    else:
        cap_counts["hit"] = 0
    return response.content
