Act on capabilities concurrently ONLY when neither works from the other's output. Two
capabilities that both consume nothing but the request, or that both consume an output
already produced, can be acted on at the same time. A capability that consumes an
output not yet produced cannot: starting it early returns `upstream_missing` and
wastes the step.

So before acting on several at once, check each one's inputs against what has actually
been produced so far. Anything whose inputs are all available goes now, together;
anything still waiting on an output goes once that output exists. Which capabilities
fall into which group is for you to work out from what each consumes and produces —
nothing here tells you.
