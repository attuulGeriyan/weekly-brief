"""All provider-specific code lives here: Gemini through its OpenAI-compatible endpoint."""
import json
import os

from openai import BadRequestError, OpenAI

BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"


def _load_env(path=".env"):
    if os.path.exists(path):
        for line in open(path):
            key, _, val = line.strip().partition("=")
            if key and not key.startswith("#"):
                os.environ.setdefault(key, val.strip("\"'"))


_load_env()
MODEL = os.environ.get("MODEL")  # no default; main.py may override with --model
_client = OpenAI(api_key=os.environ.get("GEMINI_API_KEY", "missing"), base_url=BASE_URL, max_retries=5)


def _schema(model_cls) -> dict:
    """Pydantic JSON schema -> the simpler subset Gemini accepts (inline $ref, drop null-unions)."""
    defs = model_cls.model_json_schema().get("$defs", {})

    def walk(node):
        if isinstance(node, list):
            return [walk(x) for x in node]
        if not isinstance(node, dict):
            return node
        if "$ref" in node:
            return walk(defs[node["$ref"].split("/")[-1]])
        if "anyOf" in node:
            options = [o for o in node["anyOf"] if o.get("type") != "null"]
            if len(options) == 1:
                return walk(options[0])
        return {k: walk(v) for k, v in node.items() if k not in ("$defs", "title", "default")}

    return walk(model_cls.model_json_schema())


def _spec(tool: dict) -> dict:
    params = tool["parameters"]
    if isinstance(params, type):  # a pydantic class
        params = _schema(params)
    return {"type": "function", "function": {"name": tool["name"], "description": tool["description"], "parameters": params}}


def _chat(messages, specs, tool_choice, emit):
    if not MODEL:
        raise SystemExit("MODEL is not set (put MODEL=<gemini model> in .env)")
    resp = _client.chat.completions.create(
        model=MODEL.removeprefix("models/"), temperature=0, messages=messages, tools=specs, tool_choice=tool_choice)
    choice = resp.choices[0]
    emit("llm_call", model=MODEL, tokens_in=resp.usage.prompt_tokens, tokens_out=resp.usage.completion_tokens,
         stop_reason=choice.finish_reason)
    return choice.message


def run_tool_loop(system: str, user: str, tools: list[dict], max_steps: int = 8, on_event=None) -> dict:
    """Tool-use loop. tools = [{name, description, parameters, fn, final?}].
    A tool marked final ends the loop when it returns without an "error" key.
    Returns {"final": <result of the final tool or None>, "steps": n}."""
    emit = on_event or (lambda event, **payload: None)
    by_name = {t["name"]: t for t in tools}
    specs = [_spec(t) for t in tools]
    final = next((t["name"] for t in tools if t.get("final")), None)
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    step, empty = 0, 0
    while step < max_steps + 2:
        # force the final tool from step max_steps on, or after an empty reply (forced calls are more reliable); the 2 extra steps let a rejected submission be fixed
        choice = {"type": "function", "function": {"name": final}} if final and (step + 1 >= max_steps or empty) else "auto"
        msg = _chat(messages, specs, choice, emit)
        if not msg.tool_calls:  # Gemini sometimes returns an empty/malformed reply: retry without using a step
            empty += 1
            if empty > 4:
                break
            messages.append({"role": "user", "content": f"Continue using tools, and finish by calling {final}."})
            continue
        step += 1
        messages.append(msg.model_dump(exclude_none=True))
        for call in msg.tool_calls:
            name, args = call.function.name, json.loads(call.function.arguments or "{}")
            emit("tool_call", tool=name, args=args)
            try:
                result = by_name[name]["fn"](**args)
            except Exception as e:  # bad tool name or args: tell the model, don't crash
                result = {"error": f"{type(e).__name__}: {e}"}
            text = json.dumps(result, default=str)
            emit("tool_result", tool=name, result=text[:500] + ("...[truncated]" if len(text) > 500 else ""), chars=len(text))
            messages.append({"role": "tool", "tool_call_id": call.id, "content": json.dumps(result, default=str)})
            if name == final and "error" not in result:
                return {"final": result, "steps": step}
    return {"final": None, "steps": step}


def structured(system: str, user: str, schema, on_event=None):
    """One forced tool call whose arguments are validated into `schema` (a pydantic class)."""
    emit = on_event or (lambda event, **payload: None)
    tool = {"name": "submit", "description": "Submit the result.", "parameters": schema}
    forced = {"type": "function", "function": {"name": "submit"}}
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    for attempt in range(5):  # Gemini sometimes returns an empty/malformed tool call: retry, then fail clearly
        try:
            msg = _chat(messages, [_spec(tool)], forced if attempt % 2 == 0 else "required", emit)
        except BadRequestError:  # the endpoint may reject a named tool_choice; "required" with one tool is equivalent
            msg = _chat(messages, [_spec(tool)], "required", emit)
        if msg.tool_calls:
            return schema.model_validate_json(msg.tool_calls[0].function.arguments)
    raise RuntimeError("the model returned no tool call after 5 attempts")
