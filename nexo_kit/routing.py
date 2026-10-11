"""Explicit task-to-provider routing. No guessed rankings or silent fallback."""
from __future__ import annotations

from copy import deepcopy

BACKENDS = {'codex', 'claude', 'gemini', 'command'}


def validate_profile(profile: dict) -> dict:
    if not isinstance(profile, dict) or set(profile) - {'backend', 'model', 'command'}:
        raise ValueError('Profile accepts only backend, model and command')
    backend = profile.get('backend')
    if not isinstance(backend, str) or backend not in BACKENDS:
        raise ValueError('Unknown profile backend')
    model = profile.get('model')
    if model is not None and (not isinstance(model, str) or not model.strip() or
                              model.startswith('-') or '\x00' in model or any(c.isspace() for c in model)):
        raise ValueError('model must be a nonempty model identifier')
    command = profile.get('command')
    if backend == 'command':
        if not isinstance(command, list) or not command or any(
                not isinstance(a, str) or not a or '\x00' in a for a in command):
            raise ValueError('command backend requires an explicit nonempty argv')
        if model is not None:
            raise ValueError('Set a custom command model in its argv, not in model')
    elif command is not None:
        raise ValueError('command is only supported by the command backend')
    return deepcopy(profile)


def resolve_routes(document: dict, task_ids: set[str]) -> dict[str, dict]:
    """Resolve the whole snapshot before any lease or process is created."""
    if not isinstance(document, dict) or set(document) - {'version', 'profiles', 'tasks', 'default'}:
        raise ValueError('Routing accepts only version, profiles, tasks and default')
    if type(document.get('version')) is not int or document['version'] != 1:
        raise ValueError('Routing version must be 1')
    profiles, assignments = document.get('profiles'), document.get('tasks', {})
    if not isinstance(profiles, dict) or not profiles:
        raise ValueError('Routing requires named profiles')
    if not isinstance(assignments, dict) or set(assignments) - task_ids:
        raise ValueError('Routing tasks must reference existing task IDs')
    validated = {}
    for name, profile in profiles.items():
        if not isinstance(name, str) or not name.strip():
            raise ValueError('Profile names must be nonempty strings')
        validated[name] = validate_profile(profile)
    default = document.get('default')
    for name in [*assignments.values(), *([default] if default is not None else [])]:
        if not isinstance(name, str) or name not in validated:
            raise ValueError('Routing references an unknown profile')
    result = {}
    for task_id in task_ids:
        name = assignments.get(task_id, default)
        if name is None:
            raise ValueError(f'No route for task {task_id}; assign it or set default')
        result[task_id] = dict(validated[name], profile=name)
    return result
