from scl.embeddings.impl import embed

class Msg:
    def __init__(self, messages):
        self.messages = messages
        self.embed = embed(messages[0]['content'])

    def append(self, context):
        self.messages.append(context)

    def append_cap_result(self, func1_out,tool_call_id):
        self.messages.append({
                'role': 'tool',
                'content': f'{func1_out}',
                'tool_call_id': tool_call_id
        })