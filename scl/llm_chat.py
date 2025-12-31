import json
import logging
from scl.trace import tracer

@tracer.start_as_current_span("send_messages")
def send_messages(client, model, registry, messages, ToolNames, Turns):
    if Turns == 0: 
        tools_named = registry.getCapsByNames(ToolNames)
        tools_autonomy = registry.getCapsBySimilarity(messages[0]['content'])
        ## where is learn from history? 自适应
            ## 基于规则learn
                ## 比如正则表达式
                ## 指标学习
            ## RAG，KNN
                ## https://chat.deepseek.com/a/chat/s/88544894-4a24-4dfd-ac3d-54afbd6ab7cf
            ## LLM learn？ workflow memeory
                ## 大模型通过学习历史，为未来的执行写下hardcode
        tools = []
        ## todo 去重
        for tool in tools_named:
            logging.info(tool)
            if tool['type'] != "skill":
                tools.append(tool['desc'])
            tools.append(tool['desc'])
        for tool in tools_autonomy:
            logging.info(tool)
            if tool['type'] != "skill":
                tools.append(tool['desc'])
        logging.info(tools)
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            tools=tools
        )
        return response.choices[0].message
    else:
        response = client.chat.completions.create(
            model=model,
            messages=messages
                )
        return response.choices[0].message

@tracer.start_as_current_span("function_call_playground")
def function_call_playground(client, model, registry, messages, ToolNames):    
    response = send_messages(client, model, registry, messages, ToolNames,0)
    # todo, feedback loop model(langchain)
    logging.info(response)
    if response.tool_calls:
        for tool_call in response.tool_calls:
            func1_name = tool_call.function.name
            func1_args = tool_call.function.arguments
            logging.info(f"func1_name: {func1_name}, func1_args: {func1_args}")
            args_dict = json.loads(func1_args)
            func1_out = registry.call_cap_safe(func1_name,args_dict)

            messages.append(response)
            messages.append({
                'role': 'tool',
                'content': f'{func1_out}',
                'tool_call_id': tool_call.id
             })
        response = send_messages(client, model, registry, messages, ToolNames,1)
    return response.content
