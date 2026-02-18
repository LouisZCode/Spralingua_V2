"""
Here you can find all the existing agents ready to be used. Already prompt and all is set to them

You can find here:

ClientWrapper - per-client wrapper for Pipecat (creates agent + logger per connection)
agent_assembly - factory function to create a fresh LangChain agent

"""
from .pipecat_wrapper import ClientWrapper
from .conversation_agent import agent_assembly, CONVERSATIONAL_MODEL

print("Agents module loaded correctly...")