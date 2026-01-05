import json
import logging
from scl.trace import tracer
from scl.cap_reg import CapRegistry
from scl.meta.msg import Msg

@tracer.start_as_current_span("send_messages")
def send_messages(
        client, model, 
        cap_registry:CapRegistry, 
        ToolNames, # todo here, support both name and give Cap
        msg:Msg, 
        Turns):
    if Turns == 0: 
        tools_named = cap_registry.getCapsByNames(ToolNames)
        ## why not I just prvide the metrics and leave the function to user themself?
        ## limit can be auto adjust?
        ### what's the evaludation metric?
        ### default from env(x)
        ### according to what?(y)
        ### change limit from as x - y (如性能考虑，token数量)
        ## min_similarity auto adjust?
        ### what's the evaluation metric?
        ### default min_similarity is x > an env based defualt value
        ### according to y > from history table(用户确认的行为，这就是最好的学习资料)
        ### change min_similarity as x - y?
        tools_autonomy = cap_registry.getCapsBySimilarity(msg, limit=5, min_similarity=0.5)
        tools_history = cap_registry.getCapsByHistory(msg, limit=5, min_similarity=0.5)
        tools_merged = {    
            **({} if tools_named is None else tools_named),
            **({} if tools_autonomy is None else tools_autonomy),
            **({} if tools_history is None else tools_history)
        }
        tools = []
        for tool in list(tools_merged.values()):
            if tool.type != "skill":
                tools.append(tool.llm_description)
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
    response = send_messages(client, model, cap_registry, ToolNames, msg, turns)
    # todo, feedback loop model?(ref langchain)
    turns += 1
    logging.info(response)
    if response.tool_calls:
        for tool_call in response.tool_calls:
            func1_name = tool_call.function.name
            func1_args = tool_call.function.arguments
            logging.info(f"func1_name: {func1_name}, func1_args: {func1_args}")
            args_dict = json.loads(func1_args)
            cap = cap_registry.get_cap_by_name(func1_name)
            func1_out = cap_registry.call_cap_safe(cap,args_dict)
            cap_registry.record(msg, cap)

            msg.append(response)
            msg.append_cap_result(func1_out, tool_call.id)
        response = send_messages(client, model, cap_registry, ToolNames, msg, turns)
    return response.content
