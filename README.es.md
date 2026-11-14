# sprocguard

**Una red de seguridad para los stored procedures de SQL Server.**
Registra lo que un procedure hace *hoy*, deja que una persona o un agente de IA lo modifique,
y obtén un reporte claro de cada comportamiento que cambió, antes de que llegue a producción.

[Read in English](README.md)

[![PyPI](https://img.shields.io/pypi/v/sprocguard.svg)](https://pypi.org/project/sprocguard/)
[![CI](https://github.com/JheysonJhordyHuarauyaRiquelme/sprocguard/actions/workflows/ci.yml/badge.svg)](https://github.com/JheysonJhordyHuarauyaRiquelme/sprocguard/actions/workflows/ci.yml)
[![Integration](https://github.com/JheysonJhordyHuarauyaRiquelme/sprocguard/actions/workflows/integration.yml/badge.svg)](https://github.com/JheysonJhordyHuarauyaRiquelme/sprocguard/actions/workflows/integration.yml)
[![GitHub Marketplace](https://img.shields.io/badge/Marketplace-sprocguard%20verify-blue?logo=github)](https://github.com/marketplace/actions/sprocguard-verify)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

## Tres formas de usarlo

| Dónde | Cómo | Para qué |
| --- | --- | --- |
| Terminal | `pip install sprocguard` | Revisar un cambio en tu máquina |
| Agentes de IA | Servidor MCP y plugin de Claude Code ([MCP](#con-agentes-de-ia-mcp)) | Que un agente verifique sus propios cambios en SQL |
| Pull requests | [GitHub Action](#verificar-en-cada-pull-request-github-action), también en el [GitHub Marketplace](https://github.com/marketplace/actions/sprocguard-verify) | Bloquear un merge que cambia el comportamiento |

## El problema

Muchas empresas guardan sus reglas de negocio dentro de stored procedures, casi siempre sin tests.
Hoy se le pide a agentes de IA que los "limpien" u "optimicen", y un refactor que parece
equivalente puede cambiar en silencio un redondeo, un camino de error o una tabla que actualiza.

sprocguard te da **tests de caracterización** (también llamados *golden master*) en minutos:
registra lo que el procedure devuelve y lo que cambia en la base de datos, y compara cualquier
versión nueva contra ese registro.

## Qué registra

Para cada caso (un conjunto de valores de parámetros):

- cada **result set**, en orden cuando el orden es estable;
- el **valor de retorno** y los **parámetros OUTPUT**;
- el **error** que lanza, si lo hay (los errores también son comportamiento);
- las **filas insertadas, actualizadas y eliminadas** en cada tabla que escribe, incluidas las
  que escriben los procedures que llama y los triggers.

Cada caso se ejecuta **dos veces**. Los valores que cambian solos (nuevos IDENTITY, `NEWID()` y
cualquier fecha u hora tomada mientras corría el procedure, aunque sea con precisión de segundos o
de días) se detectan automáticamente y se ignoran al verificar.

## Cómo protege tus datos

| Protección | Cómo |
|---|---|
| Nunca se confirma nada | Cada ejecución corre dentro de una transacción que siempre se revierte, con `IMPLICIT_TRANSACTIONS ON` y dos niveles de anidamiento, para que un `ROLLBACK` interno o un `COMMIT` suelto del procedure no dejen cambios. |
| Solo bases de desarrollo | Se niega a conectarse a cualquier base que no esté en `allowed_databases`. |
| Sin efectos irreversibles | Rechaza por defecto los procedures que envían correos, usan `xp_cmdshell`, inician jobs, `OPENROWSET`/`OPENQUERY` o servidores vinculados: un rollback no puede deshacer eso. |
| No vuelca tablas enormes | Las tablas que superan `max_rows_per_table` no se fotografían (se reportan como omitidas). |
| Inyección SQL | Los valores siempre viajan como parámetros, nunca concatenados. |

Mientras captura, mantiene bloqueos breves en las tablas involucradas: usa una base de desarrollo.

## Instalación

Requiere Python 3.11+ y el [Microsoft ODBC Driver 17 o 18 for SQL Server](https://learn.microsoft.com/sql/connect/odbc/download-odbc-driver-for-sql-server)
(normalmente ya viene con SQL Server o SSMS). Para ver cuál tienes, ejecuta
`Get-OdbcDriver | Where-Object Name -like "*SQL Server*"` en PowerShell. Si tienes el 17,
cambia `ODBC Driver 18` por `ODBC Driver 17` en `sprocguard.toml`.

```bash
pip install sprocguard
# Autenticación SQL sin ODBC:  pip install "sprocguard[pymssql]"
```

## Prueba rápida con la base de demo

```bash
sqlcmd -S localhost -E -i examples/demo.sql        # crea DemoShop con dos procedures
sprocguard init                                     # crea sprocguard.toml; ajusta la conexión
sprocguard inspect dbo.usp_PlaceOrder               # parámetros, tablas y riesgos
sprocguard capture dbo.usp_PlaceOrder --cases examples/cases/usp_PlaceOrder.json
sqlcmd -S localhost -E -i examples/regression.sql  # un refactor "inofensivo"
sprocguard verify dbo.usp_PlaceOrder                # detecta el cambio de redondeo (código de salida 2)
```

## Verificar en cada pull request (GitHub Action)

Este repositorio también es una GitHub Action, publicada en el
[GitHub Marketplace](https://github.com/marketplace/actions/sprocguard-verify). Levanta un SQL Server temporal en Docker,
construye tu base de datos con los scripts de tu repositorio y ejecuta `sprocguard verify`. El
pull request falla si un procedure cambió su comportamiento, y el resumen del job muestra cada
diferencia.

**Comparar con la rama base** (`mode: base-branch`, desde la v0.3.0). No hay que preparar nada
en tu máquina: la action construye la base con los scripts de la rama base, captura los
baselines con tus archivos de casos, la vuelve a construir con los scripts del pull request y
verifica.

```yaml
- uses: actions/checkout@v7
- uses: JheysonJhordyHuarauyaRiquelme/sprocguard@v0.3.0
  with:
    mode: base-branch
    cases: db/cases
    setup-scripts: |
      db/schema.sql
      db/seed.sql
      db/procedures/*.sql
```

`db/cases` tiene un archivo por procedure con su nombre (`usp_PlaceOrder.json` es
`dbo.usp_PlaceOrder`), en el mismo formato que `sprocguard capture --cases`. Los procedures que
todavía no existen en la rama base se omiten. Tus scripts deben crear siempre los mismos datos
de prueba, porque los dos lados se comparan sobre los datos que construyen.

**Comparar con baselines guardados** (el modo por defecto). Guarda en el repositorio los scripts que crean una copia de desarrollo de tu base
(tablas, datos de prueba y procedures), captura los baselines en tu máquina sobre una base
creada con esos mismos scripts y sube la carpeta `.sprocguard/baselines` junto con un archivo
de configuración. La action reemplaza la conexión por la del servidor temporal; las reglas de
`[safety]` se siguen aplicando. Todas las opciones están en el [README en inglés](README.md#verify-on-every-pull-request-github-action).

## Con agentes de IA (MCP)

sprocguard incluye un servidor MCP y una [skill](skills/sprocguard/SKILL.md) que le enseña al
agente el flujo: *inspeccionar → diseñar casos → capturar → modificar → verificar*.

**Claude Code** (plugin con el servidor MCP y la skill):

```text
/plugin marketplace add JheysonJhordyHuarauyaRiquelme/sprocguard
/plugin install sprocguard@sprocguard
```

**Cualquier cliente MCP** (VS Code, Cursor, Claude Desktop...):

```json
{ "mcpServers": { "sprocguard": { "command": "sprocguard", "args": ["serve"] } } }
```

Con [uv](https://docs.astral.sh/uv/) ni siquiera necesitas instalarlo antes: usa `"command": "uvx", "args": ["sprocguard", "serve"]`.
sprocguard también está en el [MCP Registry](https://registry.modelcontextprotocol.io) como
`io.github.JheysonJhordyHuarauyaRiquelme/sprocguard`.

## Solución de problemas

**No encuentra el driver ODBC.** Revisa cuál tienes con
`Get-OdbcDriver | Where-Object Name -like "*SQL Server*"` en PowerShell. Si tienes el 17, usa
`ODBC Driver 17 for SQL Server` en la cadena `odbc` de `sprocguard.toml`.

**Login failed (autenticación SQL).** Instala `pip install "sprocguard[pymssql]"`, configura
`server` y `user` en `sprocguard.toml` y pon la contraseña en la variable de entorno indicada en
`password_env` (por defecto `SPROCGUARD_PASSWORD`): `$env:SPROCGUARD_PASSWORD = "..."` en
PowerShell o `export SPROCGUARD_PASSWORD=...` en bash.

**"Connected to database '...', which is not in [safety].allowed_databases".** sprocguard solo
se ejecuta en las bases que listas, a propósito. Si es una base de desarrollo, agrégala a
`[safety].allowed_databases`.

**Errores de certificado con ODBC Driver 18.** En servidores locales de desarrollo, agrega
`TrustServerCertificate=yes` a la cadena `odbc`.

## Limitaciones

- Aún no soporta parámetros con valores de tabla (table-valued parameters).
- Las tablas que se modifican solo con SQL dinámico no se detectan solas; agrégalas con `--watch`.
- Si el procedure falla, no se registran los result sets que devolvió antes del error (sí el error).
- La exportación a tSQLt cubre procedures de solo lectura; para los que modifican datos usa `verify`.
- Las comparaciones ignoran el orden de las filas cuando ese orden no fue estable entre las dos ejecuciones de la captura.

## Hoja de ruta

- [ ] Exportación a tSQLt con `FakeTable` para procedures que modifican datos
- [ ] Parámetros con valores de tabla (table-valued parameters)
- [ ] `sprocguard impact <tabla>`: verificar todos los procedures que usan una tabla que vas a cambiar
- [ ] Soporte para PostgreSQL
- [x] Publicación en PyPI y en el MCP Registry
- [x] GitHub Action que verifica los procedures en cada pull request
- [x] Publicación de la Action en el GitHub Marketplace
- [x] Verificar un pull request sin baselines guardados (`mode: base-branch`)

## Contribuir

Las contribuciones son bienvenidas, en español o en inglés. En [CONTRIBUTING.md](CONTRIBUTING.md)
está cómo preparar el proyecto, correr las verificaciones y abrir un pull request. Los cambios de
cada versión están en el [CHANGELOG.md](CHANGELOG.md). Si encuentras un problema de seguridad,
repórtalo en privado como indica [SECURITY.md](SECURITY.md).

## Licencia

MIT
