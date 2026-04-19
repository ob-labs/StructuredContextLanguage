# Let's keep it simple and stupid

## How we design input layers for any agent

By default, maybe it runs as a container, when we talking about container, which means OCI(open container specs).

OCI specs means disk, process, network, if we step further with OCI lifecycle, you need file as content or data or your container, and start the container on an OS. 

Hence, file listener or disk watch dog is 1st class citizen, which benefits as receiving from middle... as persistence.

Here comes network, as restful api, as container started then the CNI mount a network config to the container.

The role of restful api becomes simple, just an restful interface for data status/file status.
Which means after validate received data, write it as file and leave it to file listener.

3rd, internal logic, internal task created by business.
Just create the file and leave it to the file listener as well.

## Queue, Heap, hashmap? & how many we need

OpenClaw starts a new age that designs agent in a hierarchy way but not pipeline way.

As above, file system becomes our gateway plane. Question becomes how many status and queue there we consume data.

### Task

Any user input task/prompt is a task.(From Gateway plane)

### Cap task/hash map

If LLM wants invokes a function, then it's a Cap task. We can execute a function call and a task in parallel.
As different cap can runs in parallel, it should be a hash map.

### Awaiting Caps

If the task is waiting(blocked) by a Cap result, it's should be in Awaiting Caps heap.
it's ordered by how many todo caps.

### Awaiting approvals

If the task is waiting(blocked) by a Human approval, it's should be in Awaiting approvals.

### How to notice and round robin?

Each time use a round robin for 2 times waiting seems good.
If over 16s, which means next round is 32s, then mark as idel.
Meanwhile, has a notice for weak up.
Hence, it's an interface which has add, and consume.