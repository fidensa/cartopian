"""Task-level launch authority shared by readiness and dispatch."""


def launch_mode(content: str) -> str:
    values = []
    for line in content.splitlines():
        if line.startswith("## "):
            break
        key, separator, value = line.strip().partition(":")
        if separator and key.strip() == "Launch mode":
            values.append(value.strip())
    if len(values) > 1 or (values and values[0] not in ("auto", "native-interactive")):
        raise ValueError("Launch mode must occur once and be auto or native-interactive")
    return values[0] if values else "auto"
