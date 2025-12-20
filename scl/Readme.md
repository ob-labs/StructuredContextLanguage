
## Step 0, config Otel, LLM, RAG

## Step 1 Receive Tool reg from client
## As procedure which "pre defined" in DB
## It will receive a short description of function and a function name?... tbd?

## Step 2.1 Receive file from client(if any), file to RAG
## if any file been given, split file into chunks after markitdown and send to RAG.

## Step 2.2.A Checking history to get a process blueprint if any
## If there is a hit, skip.

## Step 2.3.A Search on RAG get relative content
## Ref OB exp 1 to set up Autonomy way.
## If specific hit from client use and break.
## If any from 2.2.A as feed back loop here to show the Autonomy of context RAG.
## Using default setting.

## Step 2.3.B Search on Registed Tools as RAG search
## Search user task and Registed Tools as RAG search in parallel.
## If there is a hit, hit parts auto assgined to following steps.
## If any from 2.2.A as feedback loop, append.
## Progressive loading as RAG search for 2.1

## Step 2.3.C Search on Memory(msg history)
## if any hit as skip, skip.(share same hit with 2.2.A)
## Search on msg history for relevant content.

## Step 2.4 Construct a task to LLM
## System prompt from user input
## User prompt as query
## 2.3.A
## 2.3.C
## Tool call(2.3.B)
## Loop terms unit it's completed or meet the max iteration

## Should support for both openAI format and anthropic format(Tue)

## Response back to client
