# Fuentes y avisos

Nexo Kit contiene una implementación y textos originales creados para esta distribución. Los repos investigados se utilizaron como fuentes de ideas y de comparación técnica. No se vendorizan sus runtimes, SKILL.md, instaladores, binarios o bibliotecas, ni se instalan como dependencias del kit.

La [tabla de procedencia](PROVENANCE.md) identifica qué técnica inspiró cada función y cómo se combinó con otras. El [inventario](INVENTORY.md) distingue código inspeccionado, documentación, hallazgos previos y afirmaciones no demostradas. Los registros [research-extra.json](research/research-extra.json) y [research-memory.json](research/research-memory.json) fijan SHA y archivos de las inspecciones nuevas.

## Licencia de esta distribución

El código y la documentación originales de Nexo se ofrecen bajo [MIT](../LICENSE), con copyright de Nexo Kit contributors. Esta licencia no modifica la de los proyectos enlazados ni concede derechos sobre su código, nombres o marcas.

Claude Code, Codex, OpenAI, Anthropic y los nombres de los repos se utilizan para identificar compatibilidad, herramientas y fuentes. Nexo no se presenta como producto oficial de esos proyectos.

## Licencias observadas en las fuentes

Los enlaces siguientes muestran algunos casos que influyeron en la decisión de escribir una implementación propia:

| Fuente | Texto o dato observado |
|---|---|
| [Compound Engineering](https://github.com/EveryInc/compound-engineering-plugin/blob/67035e931c5cb26e80f198a7019a4502a9788273/LICENSE) | MIT. |
| [Claude Code Harness](https://github.com/Chachamaru127/claude-code-harness/blob/2b2b74805321089bd9b660a1064fa97556299703/LICENSE.md) | MIT. |
| [Beads](https://github.com/gastownhall/beads/blob/fb09d9f3e476096652fbf31664c281fd53e80757/LICENSE) | MIT. |
| [Matt Pocock skills](https://github.com/mattpocock/skills/blob/49dd158d1076134a641b33efb035946536778336/LICENSE) | MIT. |
| [Hermes Agent](https://github.com/NousResearch/hermes-agent/blob/f925b01791fe052e7d55dc975750c0bd985aff91/LICENSE) | MIT. |
| [claude-mem](https://github.com/thedotmack/claude-mem/blob/fa8ab09f06aa05f958c5225cf3756ce52a3ebb96/LICENSE) | Apache-2.0. |
| [agentmemory](https://github.com/rohitg00/agentmemory/blob/7ee162844956626d8b00ba459df169d542d4af9d/LICENSE) | Apache-2.0. |
| [context-mode](https://github.com/mksglu/context-mode/blob/8de5f73af9365d459bdfc564c045948cc3fc488b/LICENSE) | Elastic License 2.0; no se incluye su código. |
| [Task Observer](https://github.com/rebelytics/one-skill-to-rule-them-all/blob/56e890712273ce231b478adc56ee2eb81101c3d6/LICENSE.txt) | CC-BY-4.0; se atribuye la idea, sin redistribuir su contenido. |
| [pro-workflow](https://github.com/rohitg00/pro-workflow/blob/86d5f2770bd8e1695e40888e4242841eb7cbad4f/package.json) | MIT declarada en package.json; no se encontró LICENSE raíz en la revisión. No se copia implementación. |
| [Anthropic skills](https://github.com/anthropics/skills) | Condiciones por skill; no se interpreta una licencia única para todo el catálogo. |
| [OpenAI plugins](https://github.com/openai/plugins) | Condiciones por componente según la investigación previa; no se redistribuye el catálogo. |

El inventario contiene el resto de fuentes. Una URL con SHA identifica lo observado en esa revisión; una rama puede cambiar después.

## Herramientas de validación

Se utilizó el validador de Skill Creator instalado en el entorno de desarrollo. PyYAML se obtuvo únicamente en una carpeta temporal para ejecutar ese validador; no se incluye en la distribución ni se requiere para usar Nexo.

La biblioteca estándar de Python proporciona SQLite, procesos, JSON y las demás capacidades del runtime. Git y los clientes de agentes son ejecutables externos del usuario, con sus propias distribuciones y términos.

## Cambios futuros

Si una versión posterior incorpora código o textos externos sustanciales, deberá registrar su procedencia, revisión, licencia y avisos de forma específica. No se deben extender a Nexo las cifras de rendimiento, resultados de tests o garantías que publique un upstream sin volver a comprobarlas.
