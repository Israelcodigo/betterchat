# Proveedores y selección por tarea

Nexo 1.3 añade `--model` y `--routing-file`. Son dos modos excluyentes:

```powershell
python nexo.py run --backend codex --model TU_ID_DE_MODELO
python nexo.py run --routing-file routes.json
```

Una ruta contiene `version: 1`, `profiles`, asignaciones `tasks` por ID exacto y,
opcionalmente, `default`. Cada perfil admite `backend`, `model` o `command`.
Todos los IDs de tareas del registro deben tener ruta o perfil predeterminado,
incluidos los ya terminados. Un nombre desconocido o una tarea sin ruta cancela
la ejecución antes de reclamar trabajo. No hay fallback automático.

```json
{
  "version": 1,
  "profiles": {
    "implementation": {"backend": "codex"},
    "review": {"backend": "claude"},
    "analysis": {"backend": "gemini"}
  },
  "default": "implementation",
  "tasks": {"review-api": "review", "analyze-data": "analysis"}
}
```

Los nombres de perfiles no asignan roles por sí mismos. El contrato de tarea
define si el trabajador implementa, analiza o revisa. Configura checks que prueben
el entregable correspondiente: un test de producto no valida la calidad de un informe.
La revisión entre proveedores requiere dos tareas y una dependencia explícita.

## Comandos nativos

| Backend | Interfaz utilizada | Modelo |
|---|---|---|
| codex | `codex exec --sandbox workspace-write --json ... -` | `--model ID` |
| claude | `claude -p --output-format json` | `--model ID` |
| gemini | `gemini --output-format json` con stdin no interactivo | `--model ID` |

El prompt entra por stdin; el cwd es el worktree. Los permisos y la autenticación
pertenecen al cliente. Nexo no activa aprobación automática para Claude/Gemini:
una edición sin permiso puede fallar y se conserva como fallo. En Windows un
wrapper `.cmd` puede tener restricciones propias; comprueba tu distribución.

Cada ejecución guarda `request.json`, stdout/stderr y `result.json` localmente.
Se registra `requested_model`, no un modelo observado inventado; `null` significa
usar la configuración del cliente. El tiempo incluye preparación y verificación.
Una salida JSON con error, incluso multilínea y exit code cero, impide completar.

## Modelos locales y otros agentes

El backend `command` admite una lista de argumentos sin shell. Ejemplo de contrato
para **tu propio agente** (no se suministra `my_agent.py`):

```json
{"backend":"command","command":["{python}","/ruta/my_agent.py","--prompt","{prompt_file}","--answer","{answer_file}"]}
```

El agente recibe stdin, cwd y `NEXO_TASK_ID`, `NEXO_PROJECT`, `NEXO_WORKSPACE`.
Debe modificar el workspace y terminar; Nexo comprueba el resultado. Un servidor
de inferencia que solo devuelve texto necesita un adaptador de edición. No se
anuncia soporte nativo para Ollama, vLLM u otros sin esa prueba.

## Fuentes oficiales consultadas el 11 de octubre de 2026

- [Codex: ejecución no interactiva](https://learn.chatgpt.com/docs/non-interactive-mode).
- [Claude Code: ejecución programática](https://code.claude.com/docs/en/headless).
- [Gemini CLI: modo headless y errores JSON](https://geminicli.com/docs/cli/headless/).

La documentación de un cliente no acredita que esta máquina lo tenga instalado
o que una cuenta pueda usar un modelo determinado. Los tests offline y las
pruebas autenticadas se informan por separado.
