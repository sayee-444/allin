"""#121: does the chat template take a user message right after a tool's output?"""
import json
import sys

import httpx2 as httpx

BASE = "http://127.0.0.1:9931"
TOOLS = [{"type": "function", "name": "nap", "description": "Sleep a while.",
          "parameters": {"type": "object", "properties": {"seconds": {"type": "number"}},
                         "required": ["seconds"]}}]
CALL = {"type": "function_call", "call_id": "call_1", "name": "nap",
        "arguments": json.dumps({"seconds": 5})}
OUTPUT = {"type": "function_call_output", "call_id": "call_1", "output": '"rested"'}
LATE = "Mientras dormías cambié de idea: responde solo con la palabra MANZANA."

messages = [
    {"role": "user", "content": "Duerme 5 segundos con la tool nap y luego dime 'hecho'."},
    {"role": "assistant", "content": "", "tool_calls": [{"id": "call_1", "type": "function",
     "function": {"name": "nap", "arguments": json.dumps({"seconds": 5})}}]},
    {"role": "tool", "tool_call_id": "call_1", "content": '"rested"'},
    {"role": "user", "content": LATE},
]
rendered = httpx.post(f"{BASE}/apply-template", json={"messages": messages}, timeout=60).json()
print("== /apply-template: how the template renders tool output then user ==")
print(rendered.get("prompt", rendered)[-700:])

body = {"model": "x", "tools": TOOLS, "max_output_tokens": 2000,
        "input": [{"role": "user", "content": messages[0]["content"]}, CALL, OUTPUT,
                  {"role": "user", "content": LATE}]}
response = httpx.post(f"{BASE}/v1/responses", json=body, timeout=600)
print("\n== /v1/responses with function_call_output then a user message ==")
print("HTTP", response.status_code)
data = response.json()
for item in data.get("output", []):
    if item.get("type") == "message":
        print("answer:", "".join(p.get("text", "") for p in item["content"]))
    elif item.get("type") == "function_call":
        print("call:", item["name"], item["arguments"])
sys.exit(0 if response.status_code == 200 else 1)
