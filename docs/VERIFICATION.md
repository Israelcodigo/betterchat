# Comprobaciones de desarrollo

Ejecuta los siguientes comandos desde la raíz del repositorio con Python y Git:

```shell
python -B -m unittest discover -s tests -v
python -B examples/demo.py
python scripts/release_audit.py
python scripts/package_release.py
```

La demo usa ejecutores deterministas y un repositorio temporal. No necesita
cuentas ni consume cuota de modelos. La auditoría revisa el inventario público
y los enlaces locales de la documentación.

## Probar un cliente instalado

Con un cliente instalado y autenticado, puedes ejecutar una prueba opcional:

```shell
python -B examples/provider_smoke.py --backend codex --run-model
```

También admite `claude`, `gemini` y `--model ID`. Esta ejecución consume la cuota
de tu cuenta y conserva sus resultados en el directorio local indicado.
Revisa la [guía de proveedores](PROVIDERS.md) antes de ejecutarla.

## Al contribuir

Incluye los comandos utilizados y el entorno necesario para reproducir tu cambio.
Mantén las credenciales, el estado `.nexo/` y los resultados de tus ejecuciones
fuera de los commits. Consulta [CONTRIBUTING.md](../CONTRIBUTING.md).
