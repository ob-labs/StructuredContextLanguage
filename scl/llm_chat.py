import json
import logging
from scl.trace import tracer
from scl.cap_reg import CapRegistry
from scl.meta.msg import Msg

@tracer.start_as_current_span("send_messages")
def send_messages(
        client, model, 
        cap_registry:CapRegistry, 
        ToolNames, 
        msg:Msg, 
        Turns):
    if Turns == 0: 
        tools_named = cap_registry.getCapsByNames(ToolNames)
        tools_autonomy = cap_registry.getCapsBySimilarity(msg.embed)
        ## where is learn from history? 自适应
            ## 基于规则learn
                ## 指标学习
        #tools_history_count = cap_registry.getCapsByShortHistory(messages[0]['content'], type="count")
            ## RAG，KNN
        #tools_history_rag = cap_registry.getCapsByShortHistory(messages[0]['content'], type="rag")
            ## Learn？ workflow memeory -- learn_by_count, learn_by_rag
                ## 大模型通过学习历史，为未来的执行写下hardcode
        tools = []
        ## todo 去重
        for tool in tools_named:
            if tool['type'] != "skill":
                tools.append(tool['desc'])
            tools.append(tool['desc'])
        for tool in tools_autonomy:
            if tool['type'] != "skill":
                tools.append(tool['desc'])
        #for tool in tools_history:
        #    if tool['type'] != "skill":
        #        tools.append(tool['desc'])
        logging.info(tools)
        logging.info(msg)
        response = client.chat.completions.create(
            model=model,
            messages=msg.messages,
            tools=tools
        )
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
    messages
    ): 
    turns = 0
    msg = Msg(messages)
    response = send_messages(client, model, cap_registry, ToolNames, msg, turns)
    # todo, feedback loop model(langchain)
    turns += 1
    logging.info(response)
    if response.tool_calls:
        for tool_call in response.tool_calls:
            func1_name = tool_call.function.name
            func1_args = tool_call.function.arguments
            logging.info(f"func1_name: {func1_name}, func1_args: {func1_args}")
            args_dict = json.loads(func1_args)
            func1_out = cap_registry.call_cap_safe(func1_name,args_dict)
            cap_registry.record(msg.embed, func1_name)

            msg.append(response)
            msg.append_cap_result(func1_out, tool_call.id)
        response = send_messages(client, model, cap_registry, ToolNames, msg, turns)
    return response.content
