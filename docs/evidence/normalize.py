def normalize(values):
    result = []
    seen = set()
    for value in values:
        label = value.strip().casefold()
        if label and label not in seen:
            seen.add(label)
            result.append(label)
    return result
