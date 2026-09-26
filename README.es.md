<p align="center">
  <a href="README.md"><em>English</em></a> |
  <a href="README.pt-BR.md"><em>Português</em></a> |
  <a href="README.es.md"><em>Español</em></a>
</p>

<h1 align="center">✧ <strong>Career</strong> <em>Agent</em> ✧</h1>

<p align="center">
  Encuentra empleos en <strong>26 fuentes integradas</strong>, entiende por qué encajan con tu búsqueda y adapta tu currículum usando experiencia que puedas respaldar.
</p>

<p align="center">
  <img
    src="docs/assets/readme/hero.png"
    alt="Career Agent"
    width="100%"
  />
</p>

<p align="center">
  <em>Ilustración del producto con ejemplos inventados, no una captura de pantalla ni resultados medidos. Las capturas reales de la demo aparecen más abajo.</em>
</p>

<p align="center">
  <a href="https://github.com/thais-stephanie/career-agent-portfolio/releases/tag/v0.2.0-beta.1">
    <img
      src="https://img.shields.io/badge/status-Beta_1-fff08a"
      alt="Beta 1"
    />
  </a>
  <a href="#resume-tailor-beta">
    <img
      src="https://img.shields.io/badge/Resume_Tailor-Beta-d8c8ff"
      alt="Resume Tailor Beta"
    />
  </a>
  <a href="docs/VALIDATION.md">
    <img
      src="https://img.shields.io/badge/pruebas_Beta_1-9.087_aprobadas-a7ebcf"
      alt="9.087 pruebas aprobadas"
    />
  </a>
  <a href="docs/INSTALL.md#which-computers-it-runs-on">
    <img
      src="https://img.shields.io/badge/Windows_11-probado-bbd6ff"
      alt="Probado en Windows 11"
    />
  </a>
</p>

<p align="center">
  <img
    src="https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white"
    alt="Python 3.12"
  />
  <img
    src="https://img.shields.io/badge/JavaScript-ES%20Modules-F7DF1E?logo=javascript&logoColor=111"
    alt="JavaScript ES Modules"
  />
  <img
    src="https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white"
    alt="FastAPI"
  />
  <img
    src="https://img.shields.io/badge/React-18-61DAFB?logo=react&logoColor=111"
    alt="React 18"
  />
  <img
    src="https://img.shields.io/badge/uv-gesti%C3%B3n%20de%20paquetes-DE5FE9"
    alt="uv"
  />
  <img
    src="https://img.shields.io/badge/local--first-arquitectura-A7EBCF"
    alt="Local-first"
  />
  <a href="LICENSE">
    <img
      src="https://img.shields.io/badge/licencia-MIT-blue"
      alt="Licencia MIT"
    />
  </a>
</p>

<p align="center">
  <strong>
    <a href="docs/INSTALL.md">Guía de instalación (en inglés)</a>
    &nbsp;·&nbsp;
    <a href="https://github.com/thais-stephanie/career-agent-portfolio/releases/tag/v0.2.0-beta.1">Descargar para Windows</a>
    &nbsp;·&nbsp;
    <a href="#capturas-de-pantalla">Ver la aplicación</a>
  </strong>
</p>

Career Agent es un espacio de búsqueda de empleo que funciona en tu propia
computadora. Recopila ofertas públicas de portales de empleo y de los sistemas
de selección de las empresas, comprueba si cada empresa puede contratarte donde
vives y le da a cada oferta una puntuación de **Search Fit** de 0 a 100 que cita
el texto de la oferta para explicarse. **Resume Tailor Beta**, incluido en la
misma descarga, prepara un currículum para una oferta a partir de experiencia
que tú confirmaste. Tus datos se guardan en archivos dentro de la carpeta de
Career Agent; nada se sube a un servidor de Career Agent, porque no existe.

> [!NOTE]
>
> Esta es la versión **v0.2.0-beta.1**: Career Agent Beta con Resume Tailor
> Beta, para quien lo ejecuta en su propia computadora. Search Fit describe
> cómo una oferta encaja con tus preferencias de búsqueda. No estima tus
> probabilidades de ser contratado o contratada.

## Instalación

Elige un camino. La [guía de instalación](docs/INSTALL.md) tiene cada paso,
escrito para quien nunca usó una terminal. Está en inglés.

- **Uso Claude Code o Codex:** pega el
  [mensaje de instalación preparado](docs/INSTALL.md#path-a-install-with-claude-code-or-codex).
  Instala Career Agent, ejecuta la demo y te explica cómo abrirlo de nuevo.
- **Quiero instalarlo por mi cuenta:**
  - **Windows:** descarga `Career-Agent-v0.2.0-beta.1-Windows.zip` en la
    [página de la versión](https://github.com/thais-stephanie/career-agent-portfolio/releases/tag/v0.2.0-beta.1),
    desbloquéalo (clic derecho, **Propiedades**, **Desbloquear**), haz clic
    derecho, elige **Extraer todo** y haz doble clic en
    **Start-Demo.cmd** en la carpeta extraída. No necesitas instalar Python
    antes: el launcher descarga uv, que instala Python 3.12 y las bibliotecas
    bloqueadas. [Paso a paso](docs/INSTALL.md#path-b-on-windows-download-and-double-click).
  - **macOS o Linux:** instala uv, extrae el archivo de código fuente y ejecuta
    dos comandos. [Paso a paso](docs/INSTALL.md#path-b-on-macos-or-linux-use-the-terminal).
    Estas plataformas no se probaron en esta versión.

La demo abre con 21 ofertas inventadas y una persona candidata inventada, Alex
Morgan, en un almacenamiento separado del tuyo. Detén la demo con **Ctrl+C**
en su ventana y haz doble clic en **Start-Career-Agent.cmd** para configurar tu
búsqueda. La guía también explica
cómo [abrirlo de nuevo](docs/INSTALL.md#how-to-open-career-agent-next-time),
[hacer copias de seguridad](docs/INSTALL.md#backups), [actualizar](docs/INSTALL.md#updating-to-a-new-version),
[resolver problemas](docs/INSTALL.md#troubleshooting) y
[desinstalar](docs/INSTALL.md#uninstalling).

## Capturas de pantalla

Las cuatro muestran el producto con datos sintéticos de la demo o con un
espacio de trabajo vacío en el primer inicio. La interfaz aparece en inglés;
también está en portugués de Brasil.

| Descubrir ofertas | Por qué encaja con tu búsqueda |
|---|---|
| ![Descubrir con ofertas sintéticas, Search Fit y Posting completeness en cada tarjeta](docs/assets/readme/discover.png) | ![Motivos de Search Fit, cada uno citado de la oferta](docs/assets/readme/why.png) |

| Resume Tailor Beta | Primer inicio |
|---|---|
| ![Resume Tailor con la persona candidata sintética Alex Morgan](docs/assets/readme/tailor.png) | ![Configuración inicial con el menú de perfil y diez preguntas cortas](docs/assets/readme/first-run.png) |

![Flujo: descubrir, entender el Search Fit, preparar con Tailor Beta y seguir postulaciones](docs/assets/readme/how-it-works.png)

*Ilustración del flujo, no una captura de pantalla.*

## Qué hace

| Área | Qué obtienes |
|---|---|
| Descubrir | Ofertas de 26 fuentes integradas, sin duplicados, en una lista, además de la importación manual de cualquier oferta. Cada oferta conserva su fuente y su historial de recopilación. Vistas Cards, Table y Board; Table exporta los resultados GOOD y STRONG actuales en un archivo CSV. |
| Elegibilidad | Las restricciones de contratación (país, permiso de trabajo, habilitación de seguridad) se revisan aparte de las preferencias. "Remoto" nunca se lee como "cualquier lugar del mundo"; una oferta que no dice dónde contrata queda sin resolver. |
| Search Fit | Una puntuación de 0 a 100 con una banda (STRONG, GOOD, MODERATE, WEAK), motivos citados de la oferta y lo que la oferta no dijo. **Posting completeness** se muestra al lado y nunca la cambia. |
| Career Evidence | Career Agent lee tu currículum en tu computadora y propone afirmaciones. Cada una entra en tu Career Profile solo cuando la confirmas. |
| Postulaciones | Estados (Found, Interested, Applied, Interviewing, Offer, Rejected y otros), notas e historial de las ofertas en las que actúas. |
| Resume Tailor Beta | Preparación de currículum para una oferta, a partir de experiencia confirmada, con exportación en Markdown, Word y PDF. |

### Qué es estable, opcional, experimental o inexistente

| Función | Estado |
|---|---|
| Recopilación de 26 fuentes, elegibilidad, Search Fit, Career Evidence, postulaciones | Parte de esta Beta |
| Perfiles locales (varias personas en una instalación) | Parte de esta Beta |
| Catálogo local compartido de ofertas | Parte de esta Beta |
| Resume Tailor | Beta |
| Semantic matching (DeepSeek, Claude Code o Codex) | Opcional, apagado hasta que inicies una ejecución |
| Lectura con modelo local (Ollama, `qwen3:4b`) | Opcional, solo en esta computadora |
| LinkedIn mediante JobSpy | Experimental, apagado por defecto, por perfil |
| Servicio alojado o multiusuario, cuentas, inicio de sesión | No implementado |
| Postulación automática | No implementado |
| Sincronización en la nube entre computadoras | No implementado |

## Cómo funciona Search Fit

Describes con tus palabras el trabajo que quieres. Career Agent compara esa
descripción con el texto de la oferta: responsabilidades, herramientas, nivel,
contrato, modalidad de trabajo y salario. Cada punto está ligado a una frase
citada de la oferta. El título solo no suma puntos.

- Una oferta que no indica el nivel se evalúa como nivel medio (Pleno).
- Un salario o contrato no indicados reciben un valor intermedio, entre
  incompatible y compatible.
- Los **role anchors** son los cargos que tienes en mente. Orientan qué
  búsquedas hace Career Agent (por ejemplo en Himalayas y LinkedIn); no suman
  puntos a una oferta.
- **Semantic matching** es opcional. Un proveedor de IA lee ofertas
  seleccionadas y dice qué partes de tu búsqueda cubre cada una, con citas.
  Career Agent acepta solo las citas que encuentra en la oferta, y su propia
  aritmética calcula la puntuación. Detalles: [SEMANTIC_MATCHING.md](docs/SEMANTIC_MATCHING.md).

Search Fit nunca lee tu evidencia confirmada, y la puntuación de coincidencia
de Resume Tailor (Tailor Match) es otro número.

## Fuentes de empleo

Career Agent cuenta con **26 fuentes de empleo integradas** con cobertura
global, de Estados Unidos, Europa, LATAM y Brasil.

| Cobertura | Fuentes integradas |
|---|---|
| ATS y sistemas de empresas | Greenhouse, Lever, Ashby, Workday, Workable, Teamtailor, Rippling, Recruitee, Comeet, Gupy |
| Portales remotos y globales | We Work Remotely, Remote OK, Himalayas, Jobicy, 4 Day Week, Remotive, Working Nomads, Dynamite Jobs, Arbeitnow |
| Redes y agregadores | a16z Speedrun, Jobgether, Jooble |
| LATAM y Brasil | Get on Board, Recruiterflow, Avlis Talent, Programathor |

Algunas fuentes ofrecen inventarios estructurados completos. Otras solo
ofrecen ventanas de ofertas recientes, feeds limitados o resultados con
metadatos, por eso Career Agent registra la cobertura de cada fuente.
**Settings & Sources** muestra una tabla de salud de las fuentes: cuándo
funcionó cada una por última vez, cuáles están pendientes (un día) o
desactualizadas (tres días), y el botón **Refresh due sources**. Nada se
recopila hasta que pulsas un botón.

[Permisos y notas de cobertura de las fuentes](docs/SOURCES.md)

### LinkedIn

LinkedIn restringe la recopilación automatizada, así que su fila de fuente
sigue marcada como prohibida. Cada persona puede elegir una excepción
**experimental** para su propio perfil: **LinkedIn via JobSpy**, apagada por
defecto y activada solo después de un aviso en Settings & Sources. Hace un
número limitado de búsquedas (24 por defecto) a partir de los role anchors y
las frases de trabajo, nunca inicia sesión y no guarda contraseña, cookie ni
sesión de LinkedIn. Cuando LinkedIn rechaza las solicitudes, la fuente se
detiene y espera un día en lugar de reintentar. También puedes pegar cualquier
oferta de LinkedIn en la importación manual.

## Perfiles locales y catálogo compartido

Una instalación puede tener varios **perfiles locales**, por ejemplo tú y
alguien de tu familia. Cada perfil tiene su propia configuración, role anchors,
currículum, evidencia confirmada, puntuaciones de Search Fit, postulaciones,
notas y espacio de Resume Tailor. Los datos de un perfil nunca aparecen en otro
ni lo influyen.

Los datos públicos de empleo (ofertas, descripciones, empresas, portales y el
índice de búsqueda) se guardan una sola vez en `data/shared/catalogue.db`, y
todos los perfiles los leen. Qué búsqueda de qué perfil encontró una oferta
queda en ese perfil.

Los perfiles **no son cuentas**. No hay inicio de sesión ni contraseña, y
cualquier persona que use la misma cuenta del sistema operativo puede leer los
archivos de todos los perfiles. Detalles: [MULTI_PROFILE.md](docs/MULTI_PROFILE.md).

## Resume Tailor Beta

Abre **Resume Tailor** desde la barra lateral o desde los detalles de una
oferta. Sigue el perfil local activo, abre en la oferta de la que vienes y
lista las ofertas que sigues. Un currículum base puede venir de tu Career
Profile confirmado o de un archivo PDF, Word (.docx) o Markdown que subas.

Analiza la oferta, relaciona sus requisitos con tu evidencia, muestra brechas,
genera un borrador, lo valida, te deja editarlo y exporta Markdown, Word o
PDF. La exportación a Markdown y Word funciona sin IA. El PDF necesita
Microsoft Word o LibreOffice en la computadora; sin ellos, Resume Tailor
indica que el PDF no está disponible.

Las afirmaciones confirmadas del Career Profile pasan a Resume Tailor solo
cuando eliges **Use my Career Profile** o actualizas desde él; las que siguen
en revisión nunca pasan. Una frase que genera Resume Tailor nunca se convierte
en evidencia confirmada, y una edición que afirma experiencia sin respaldo es
rechazada por la exportación con solo evidencia. Consulta las
[notas de arquitectura](docs/ARCHITECTURE.md).

## Privacidad

Tu configuración, ofertas, puntuaciones de Search Fit, notas, postulaciones,
texto del currículum y evidencia se guardan en las carpetas `data` y `config`,
dentro de la carpeta de Career Agent. Career Agent guarda el texto que extrae
de un currículum, no el archivo subido. Resume Tailor guarda los documentos que
le subes. No hay telemetría, y la aplicación no cifra sus archivos.

Estas acciones envían datos fuera de la computadora, cada una solo cuando la
haces:

| Cuándo | Qué sale |
|---|---|
| Primera instalación | Descargas de uv, Python y las bibliotecas bloqueadas. |
| Recopilar ofertas | Solicitudes a portales de empleo y sitios de empresas, con frases de búsqueda y lugares. Nunca tu currículum ni tu perfil. |
| LinkedIn via JobSpy (si lo activaste) | Frases de búsqueda cortas y lugares, enviados a LinkedIn. |
| Semantic matching (si inicias una ejecución) | Tus frases de búsqueda y el texto de las ofertas seleccionadas, al proveedor elegido. |
| IA de Resume Tailor (si configuraste una) | Descripciones de ofertas y evidencia seleccionada, a ese proveedor. |
| Abrir el enlace de una empresa | Tu navegador visita ese sitio. |

La lectura con modelo local (Ollama) se queda en la computadora: Career Agent
rechaza una dirección de Ollama que no sea local. El inventario completo está
en [PRIVACY.md](docs/PRIVACY.md).

## Validación

La verificación de la versión v0.2.0-beta.1 pasó **9.087 pruebas**, con 7
omitidas y 0 fallos, en Windows 11 con Python 3.12:

| Conjunto | Aprobadas |
|---|---:|
| Career Agent, unitarias | 7.001 |
| Career Agent, integración | 1.656 |
| Career Agent, navegador | 370 |
| Resume Tailor, Python | 22 |
| Resume Tailor, frontend | 38 |

Ruff, la verificación de formato, mypy y las verificaciones del frontend
pasaron. El ZIP de la versión se instaló en una carpeta nueva cuya ruta tiene
espacios, sin uv ni Python disponibles antes, y se comprobaron la demo, el modo
personal, un conflicto de puertos, el reinicio y el cierre. No hay integración
continua: la verificación se ejecuta en la computadora Windows de quien
mantiene el proyecto. La [validación de la versión](docs/VALIDATION.md) lista
las pruebas omitidas y las comprobaciones de instalación.

## Limitaciones conocidas

- Windows 11 es la única plataforma probada en esta versión. Hay
  instrucciones para macOS y Linux, pero no se ejecutaron.
- Los perfiles locales separan datos dentro de la aplicación. No son una
  barrera de seguridad.
- Search Fit: una revisión manual de resultados recientes encontró ofertas
  GOOD respaldadas sobre todo por frases de trabajo genéricas. La puntuación no
  cambió, porque un cambio necesita antes un benchmark de calidad de la
  evidencia.
- La recopilación de LinkedIn es experimental. LinkedIn puede rechazar o
  limitar las solicitudes, y una fuente rechazada espera un día.
- La disponibilidad de las fuentes cambia, y algunas devuelven solo ventanas
  recientes o metadatos. Una oferta que no dice dónde contrata queda sin
  resolver.
- La interfaz de Career Agent está en inglés y portugués de Brasil; no hay
  interfaz en español. La de Resume Tailor está en inglés; sus mensajes de
  error están en inglés y portugués de Brasil.
- La lectura con modelo local tarda minutos en el procesador de una laptop.
- La exportación a PDF necesita Word o LibreOffice. LibreOffice no se probó.
- No se llamó a ningún proveedor de IA alojado durante las pruebas de la
  versión.
- Algunos conjuntos de validación usados en el desarrollo son privados y no se
  distribuyen; sus pruebas quedan fuera y no cuentan como aprobadas.

## Desarrollo

Consulta [CONTRIBUTING.md](CONTRIBUTING.md) para los comandos de desarrollo y
la verificación de versión. El bundle del frontend de Tailor está incluido;
Node solo se necesita para recompilarlo. [Arquitectura](docs/ARCHITECTURE.md),
[permisos de las fuentes](docs/SOURCES.md) y [auditoría pública](docs/PUBLIC_AUDIT.md)
explican los límites de ingeniería.

## Licencia y avisos de terceros

Career Agent usa la licencia [MIT](LICENSE). Resume Tailor, en `companion/resume-tailor`, usa [Apache-2.0](companion/resume-tailor/LICENSE). Las fuentes tipográficas incluidas conservan sus licencias OFL. Consulta [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) para los límites entre componentes, dependencias y atribuciones.

Crédito a [Career-Ops](https://github.com/career-ops-hq/career-ops) por los patrones de protocolo que orientaron el trabajo en los adaptadores. Referencias de presentación: [ECC](https://github.com/affaan-m/ECC), [Open Code Review](https://github.com/alibaba/open-code-review), [Ponytail](https://github.com/DietrichGebert/ponytail), [Colibri](https://github.com/JustVugg/colibri). Los avisos distinguen material adaptado, conocimiento de protocolo, inspiración y dependencias. No se implica ninguna afiliación.
