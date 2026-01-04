from scl.embeddings.impl import embed

class Msg:
    def __init__(self, messages):
        self._messages = messages
        self._embed = embed(messages[0]['content'])

    def append(self, context):
        self._messages.append(context)

    def append_cap_result(self, func1_out,tool_call_id):
        self._messages.append({
                'role': 'tool',
                'content': f'{func1_out}',
                'tool_call_id': tool_call_id
        })

    @property
    def messages(self):
        return self._messages

    @property
    def embed(self):
        return self._embed
