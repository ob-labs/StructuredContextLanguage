This is a harness prompt template used to generate code for this repo.

```
<role>
<case>
<target>
<constraints>
<reference>
```

Generally and also as feature in PR should have as:

```
You are a software developer.
We need to completed the feature in python code.
You will impl python code as repsonse.

Targets:
We are going to design a main.py with main function as entry of the project.
In current phase, we will focus on how this project receive inputs from other parts.

Assumption:
- You can create an class as todo, don't need impl it for now.

Project Constraints:
- please impls in python.
- please relay on otel for tracing, metric, logs.
- please design log for info and debug level.
- Any status change/business action should be reported as a metric.
- Any status change/business action should have a log record.
- for any python dependency, provides install script as pip install instead of requirements.txt.

Business Constraints:
- Once we start this code, we expected receive data from:
1. rest api
2. listen to file system change, as new file added into todo folder.
3. todo item generated during item processing.

Testing Constraints:
- You need to provide script to prepare env.
- You need to provide a script to test the code.
```

Which one step further, we suppose we can attempt with self explain style:
```
You are a software developer.
You will see a file, the features and design goals are listed at beginning of the file.
Please let me know once you are ready.

Targets:
Your will compare which features are implemented and which are not.
If there any feature not been implemented please implements it.
Consider we are doing opensource project, some features may missing in the comments.
You should list missing features as well but keep them. 
You will return with full file after updated.

Assumption:
- For any unknown api usage, please ask human's help or impls a fake.

Project Constraints:
- please relay on otel for tracing, metric, logs.
- please design log for info and debug level.
- for any python dependency, provides install script as pip install instead of requirements.txt.

Business Constraints:
Please read from comments as beginning of the input code.

Reference coding rules:
OTEL:
import logging
from scl.otel.otel import tracer,meter
from opentelemetry import trace

class example:
    def __init__(self):
        self.some_counter = meter.create_counter(
            "business",
            description="business"
        )
        self.logger = logging.getLogger(__name__)

    @tracer.start_as_current_span("function...")
    def function(self...)
        current_span = trace.get_current_span()
        ##update span....
        ##business impls(either status change or invoke other packages)
        self.logger.debug("debug msg for business impls")
        self.some_counter.add(1) # metric changes
```