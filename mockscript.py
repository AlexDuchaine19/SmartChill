import json

def from_json_to_pretty_msg(json_str: str) -> str:
    data = json.loads(json_str)
    return "\n".join(f"{key}: {value}" for key, value in data.items())
