### Serializing multiagent prompts in one rtx 5090 without the needing of a parallel agent running:

Implementing new concepts about function tools.
Asynchronous modality when a second LLM plug in.
The "main" concept here is to refer to the main process that process, create turns, serialize works, queue the works and create an hierarchy of process and what to do in order. A main create a submain and call it.
The mains can create windows (sessions) where you can input a prompt, the prompt travels across is main, the main create a turn and put in in a queue as they come in, it can organizate complex agent structures with a single GPU with the cost of time but gaining power and context lenght due multiagent nature.

#### Function tools:

All of these functions derives from the concept of function tool, there's nothing new but complex functionalities born from these:


- Function source: Function tool that calls a command from main. New commands cannot be implemented in run time

- Function agent: Function that doesn't call commands form main and can be implemented in run time.

- Function screen: Is a function that adhere to a main process, creates command methods that can be called as command a **function source**. This is a more complex process that the user, and multiple sessions with the correct function source methods inherited from main, a session can manipulate a process. e.g. Connect to an API without the needing of create new code in the source code, implementing a new function screen in run time can lead to complex structures of profiles, works, behavior of your profile (set of mains and its sessions and rules such function tools from third or parties) and most of the time unexpected if there's not a system prompt to manage correctly the implementation of a function screen.