# Verificación de Nexo 1.3.0rc1

Fecha: 11 de octubre de 2026. Plataforma local: Windows, Python 3.13.15,
Git y PowerShell. La CI remota se prepara para Windows/Linux y Python 3.11/3.13;
su mera configuración no prueba que esos cuatro entornos hayan pasado.

## Evidencia

| Comprobación | Resultado |
|---|---|
| Base recuperada 1.2.2 | 278 tests pasaron antes de los cambios |
| Nuevas rutas por tarea | 10 tests, incluidos procesos y worktrees reales, errores JSON y rechazo de CLI ambiguo |
| Suite final | 288 tests pasaron en 119,456 segundos |
| Demo offline | 3 tareas verificadas e integradas; fallo deliberado reparado; 5,217 segundos |
| Distribución | Wheel instalado aisladamente: runtime, panel y 9 skills presentes |
| Codex real | CLI 0.162.0-alpha.17.2, 4 casos pasados, `done`, 38,509 segundos |
| Claude/Gemini reales | No disponibles en PATH; no ejecutados |
| Mejoría frente a modelos individuales | No medida |

El [resumen público del smoke](evidence/codex-smoke.json) omite rutas privadas y
trazas de cuenta. Incluye el hash SHA-256 del [código producido](evidence/normalize.py).
`release_audit.py` vuelve a comprobar el hash y los cuatro casos. Esto acredita
la integridad del artefacto publicado; la atribución al proveedor proviene del
ensayo local observado, no de una firma del proveedor.

El modelo solicitado fue el predeterminado del cliente, sin fijar un ID.
No se infiere el modelo efectivo ni el coste. El ensayo mantuvo el sandbox
`workspace-write`; no alteró permisos globales. Los casos se definieron en el
coordinador fuera del worktree del trabajador. Un smoke de normalización no
representa la dificultad de proyectos grandes.

## Reproducir

```powershell
python -B -m unittest discover -s tests -v
python -B examples/demo.py
python scripts/release_audit.py
python scripts/package_release.py
```

Prueba autenticada opcional, con consumo de cuota:

```powershell
python -B examples/provider_smoke.py --backend codex --run-model
```

La suite comprueba estados, leases, tokens obsoletos, recuperación, MCP, instalación,
evidencia, cancelación, rutas y fallos de proveedores. Los mocks y procesos de
prueba permanecen diferenciados del smoke real. Un resultado favorable no prueba
ausencia de defectos. No se publican logs ni estados personales de otros proyectos.
