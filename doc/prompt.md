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
