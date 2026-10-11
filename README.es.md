![BetterChat: agentes coordinados y resultados verificables](docs/assets/banner.svg)

# BetterChat

[English](README.md) · [Cambios de versión](CHANGELOG.md)

**BetterChat es la presentación pública del kit, con Nexo como motor.**
El paquete sigue llamándose `nexo-agent-kit` y el comando sigue siendo `nexo`.
Es una herramienta para coordinar agentes de programación, con CLI y panel local.

**Distintos modelos. Un estado de proyecto. Resultados verificables.**

Nexo coordina trabajo de programación entre clientes de modelos: tareas con
dependencias, perfiles por tarea, worktrees Git, ejecución acotada, checks,
memoria local y recuperación. Python 3.11+, sin dependencias de runtime.

Versión candidata **1.3.0rc1**. Windows es la plataforma comprobada localmente.
La CI incluye Linux; su resultado remoto debe comprobarse tras publicar.

## Pruébalo sin claves ni modelos

Desde la raíz del repositorio, con Python y Git instalados:

```powershell
git clone https://github.com/Israelcodigo/betterchat.git
cd betterchat
python -B -m unittest discover -s tests -v
python -B examples/demo.py
```

La demo ejecuta tres tareas, provoca un fallo, lo repara, transmite dependencias
entre worktrees e integra el resultado con tests. Conserva su directorio temporal
y devuelve dónde consultar los recibos. **Usa ejecutores de prueba, no modelos.**

## Combinar proveedores

Cada tarea puede usar un perfil con su propio proveedor y modelo. El identificador
del modelo es explícito o se hereda del cliente; Nexo no inventa cuál es «el mejor».

| Ejecutor | Integración | Evidencia de esta entrega |
|---|---|---|
| OpenAI Codex CLI | Nativa; sandbox `workspace-write` | Prueba real en [verificación](docs/VERIFICATION.md) |
| Anthropic Claude Code | Nativa; permisos del cliente | Contrato local; falta prueba real |
| Google Gemini CLI | Nativa, modo no interactivo | Contrato local; falta prueba real |
| Otros / modelos locales | Comando explícito con argv y stdin | Proceso de prueba; requiere un agente que edite archivos |

Un ejemplo de rutas para tareas existentes llamadas `implement` y `review`:

```json
{
  "version": 1,
  "profiles": {
    "builder": {"backend": "codex"},
    "reviewer": {"backend": "claude"}
  },
  "tasks": {"implement": "builder", "review": "reviewer"}
}
```

```powershell
python nexo.py --project C:\dev\mi-proyecto run --routing-file routes.json --workers 1 --cycles 1
```

El proyecto debe estar inicializado, tener un commit, checks adecuados y las
tareas creadas. Declara `review` dependiente de `implement` para transmitir los
cambios. [Guía completa de proveedores y rutas](docs/PROVIDERS.md).

## Una prueba real, opcional

Con el cliente instalado y autenticado; consume su cuota:

```powershell
python -B examples/provider_smoke.py --backend codex --run-model
```

También acepta `claude`, `gemini` y `--model ID`. El cliente repara un módulo en un
repositorio desechable. El coordinador ejecuta cuatro casos externos al worktree.
Si falta el cliente, falla sin fingir una ejecución. No cambia modelos, confianza
ni permisos globales.

## Instalar

```powershell
python -m pip install .
nexo init C:\dev\mi-proyecto --state-only
```

Para skills, roles, hooks, MCP y panel, consulta el [manual](docs/MANUAL.md).
El estado vive en `.nexo/` y no se publica. La integración nativa de skills y hooks
está orientada a Codex y Claude; el adaptador Gemini ejecuta tareas, no instala
integraciones de cliente.

## Qué queda demostrado

Un trabajador con salida cero aún debe superar los checks para llegar a `done`.
Los recibos identifican contenido y configuración, y se rechazan si cambian.
Los tests cubren dependencias, leases, fallos, cancelación, instalación y rutas.
Consulta [resultados y límites](docs/VERIFICATION.md).

**No hay todavía evidencia de que mezclar proveedores supere al mejor modelo
individual.** El [protocolo de evaluación](docs/EVALUATION.md) define cómo medirlo
con tareas iguales, presupuestos comparables y pruebas externas. El proyecto no
fusiona pesos ni entrena modelos.

## Contribuir

Lee [CONTRIBUTING.md](CONTRIBUTING.md), [seguridad](SECURITY.md),
[diseño](docs/DESIGN.md) y [procedencia](docs/PROVENANCE.md).
Las ideas de otros proyectos se atribuyen; sus runtimes no se redistribuyen.
Licencia [MIT](LICENSE).
