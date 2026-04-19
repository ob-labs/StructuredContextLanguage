# listener package 

1. Recevie data from 
- folder scan as file_watch.py as 1st class citizen.
- for either restful or internal, should write file, so that every thing can be recorded on disk by design.
- rest api as restful_watch.py
- internal produced as interal_watch.py

1. Ask Human to interaction can been seen as combine of internal produced and restful.

1. OTEL
- Put into Queue action
- Metrics defined in each file as how many been input into queue

1. Event(tbd)
- Event to notice taskQueue.

1. Config able(tbd)
- Need configurable start as container start considering.