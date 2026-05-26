from __future__ import annotations


def openai_json_schema(model: type) -> dict:
    """Prepare a Pydantic schema for OpenAI strict structured outputs."""
    schema = model.model_json_schema()

    def patch(node: dict) -> None:
        if not isinstance(node, dict):
            return
        if node.get("type") == "object" or "properties" in node:
            node["additionalProperties"] = False
        for key in ("$defs", "definitions", "properties", "items", "anyOf", "allOf", "oneOf"):
            value = node.get(key)
            if isinstance(value, dict):
                if key in ("$defs", "definitions", "properties"):
                    for child in value.values():
                        patch(child)
                else:
                    patch(value)
            elif isinstance(value, list):
                for child in value:
                    patch(child)

    patch(schema)
    if "properties" in schema:
        # OpenAI strict mode requires every property in `required`
        schema["required"] = list(schema["properties"].keys())
    schema.pop("$schema", None)
    return schema
