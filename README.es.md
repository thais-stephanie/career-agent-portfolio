[English](README.md) | [Português](README.pt-BR.md) | [Español](README.es.md)

# Career Agent

Encuentra ofertas, entiende cómo encajan con tu búsqueda y adapta tu currículum con experiencia que puedes respaldar.

![Career Agent](docs/assets/readme/hero.png)

*Ilustración del producto con ejemplos inventados, no una captura de pantalla ni resultados medidos. Más abajo encontrarás capturas reales de la demo.*

[![Alpha 2](https://img.shields.io/badge/status-Alpha_2-fff08a)](https://github.com/thais-stephanie/career-agent-portfolio/releases/tag/v0.1.0-alpha.2) [![Resume Tailor Beta](https://img.shields.io/badge/Resume_Tailor-Beta-d8c8ff)](#resume-tailor-beta) [![Alpha 2 tests](https://img.shields.io/badge/Alpha_2_tests-7%2C232_passed-a7ebcf)](https://github.com/thais-stephanie/career-agent-portfolio/blob/v0.1.0-alpha.2/docs/VALIDATION.md) [![Windows validated for Alpha 2](https://img.shields.io/badge/Alpha_2-Windows_validated-bbd6ff)](https://github.com/thais-stephanie/career-agent-portfolio/releases/tag/v0.1.0-alpha.2)

**[Descargar para Windows](https://github.com/thais-stephanie/career-agent-portfolio/releases/tag/v0.1.0-alpha.2) · [Probar la demo](#demo) · [Ver la aplicación](#recorrido-visual)**

## Estado del proyecto
**Alpha v0.1.0-alpha.2**, con **Resume Tailor Beta** incluido. Es una aplicación local funcional, todavía en desarrollo. Search Fit explica tus preferencias de búsqueda; no indica la probabilidad de contratación.

## Instalación más sencilla: Windows

1. Abre [Releases](https://github.com/thais-stephanie/career-agent-portfolio/releases/tag/v0.1.0-alpha.2).
2. Descarga `Career-Agent-v0.1.0-alpha.2-Windows.zip`.
3. Haz clic derecho en el ZIP, elige **Extraer todo** y abre la carpeta extraída.
4. Haz doble clic en **Start-Career-Agent.cmd**. Deja abierta esa ventana.
5. Espera a que termine la preparación. El navegador se abrirá automáticamente.

No necesitas instalar Python ni Node. El lanzador descarga uv, una herramienta que gestiona Python y paquetes, si hace falta; uv instala Python 3.12 y las dependencias en las versiones registradas. La primera instalación necesita internet. Usa una carpeta donde puedas guardar archivos, fuera de Archivos de programa. [Ayuda para empezar](FIRST_RUN.md).

## Cómo volver a abrirlo
Haz doble clic en el mismo lanzador, en la misma carpeta. Tus datos permanecen allí. Pulsa **Ctrl+C** en su ventana para cerrar las dos aplicaciones. Haz una copia de seguridad antes de cambiar de versión.

## Qué hace

| Etapa | Qué obtienes |
|---|---|
| Descubrir ofertas | Más de 25 conectores para sistemas de empleadores, portales de trabajo remoto y agregadores, además de la importación manual. La disponibilidad y configuración varían según la fuente. |
| Comprobar elegibilidad | Las restricciones de contratación se separan de tus preferencias. “Remoto” no significa contratación mundial; los datos que faltan quedan pendientes. |
| Entender Search Fit | Una puntuación transparente que compara datos de la oferta con tus preferencias de búsqueda, mostrando motivos e información ausente. No es una probabilidad de contratación. |
| Revisar Career Evidence | Extrae sugerencias del currículum y confírmalas, edítalas o recházalas. Importar texto no confirma una experiencia. |
| Hacer seguimiento | Guarda el estado y las notas de las candidaturas que decides enviar. |

## Resume Tailor Beta
Abre **Resume Tailor Beta** en el menú lateral. Desde una oferta, elige **Copy job description**, abre Tailor y pega el texto en **Tailor resume**.

Crea un perfil de candidato, añade un currículum base y fuentes, revisa las evidencias, analiza la oferta, examina coincidencias y carencias, genera, edita, valida y exporta. Markdown y Word funcionan sin IA. PDF necesita Microsoft Word o LibreOffice instalado localmente; si no está disponible ninguno, la aplicación lo explica.

Los módulos almacenan evidencias por separado. No sincronizan perfiles ni evidencias automáticamente. **Search Fit y Tailor Match responden a preguntas distintas.** El texto generado nunca se convierte en Career Evidence confirmada. La experiencia sin respaldo no debe aparecer como afirmación en el currículum. Consulta las [garantías de honestidad](docs/ARCHITECTURE.md).

## Recorrido visual

![Flujo: descubrir ofertas, examinar Search Fit y motivos, preparar con Tailor Beta y seguir candidaturas](docs/assets/readme/how-it-works.png)

*Ilustración del flujo. Las capturas siguientes muestran la aplicación real con datos ficticios de demostración.*

| Descubrir ofertas | Entender “Why this matches” |
|---|---|
| ![Discover con ofertas ficticias](docs/assets/readme/discover.png) | ![Motivos de Search Fit e información ausente](docs/assets/readme/why.png) |

| Preparar con Resume Tailor Beta | Crear tu espacio de trabajo |
|---|---|
| ![Resume Tailor Beta](docs/assets/readme/tailor.png) | ![Configuración del primer uso](docs/assets/readme/first-run.png) |

## Privacidad
Configuración, ofertas, notas y evidencias se guardan localmente. Tailor conserva los documentos añadidos como fuentes; Career Agent extrae el texto del CV sin guardar el archivo subido. Los proveedores opcionales de IA pueden recibir contenido cuando los configuras y utilizas. La recopilación de ofertas y los enlaces a empleadores también usan internet. No hay telemetría implementada. Lee el [modelo de privacidad](docs/PRIVACY.md), incluidas las copias de seguridad y el portapapeles.

## Qué no hace deliberadamente
No envía candidaturas automáticamente, no promete entrevistas, no interpreta trabajo remoto como elegibilidad mundial, no oculta carencias ni trata texto de IA como experiencia confirmada.

## Instalación desde el terminal

<details>
<summary>Configuración manual para desarrolladores</summary>

**Windows: abre Inicio, escribe PowerShell y abre Windows PowerShell.** En macOS, abre Aplicaciones → Utilidades → Terminal. En Linux, abre tu aplicación Terminal.

Instala [uv](https://docs.astral.sh/uv/getting-started/installation/), descarga y extrae el ZIP del código fuente en [Releases](https://github.com/thais-stephanie/career-agent-portfolio/releases/tag/v0.1.0-alpha.2). Escribe `cd` seguido de la ruta de la carpeta entre comillas. Ejecuta:

```powershell
uv sync --locked --python 3.12
uv run python scripts/launch.py
```

No hace falta instalar Python manualmente. Windows es la plataforma probada para este release. Para usar otros puertos: `uv run python scripts/launch.py --port 8875` (Tailor usará 8876).

</details>

## Demo
Haz doble clic en **Start-Demo.cmd**, o ejecuta `uv run python scripts/launch.py --demo`. Crea ofertas inventadas y Alex Morgan, un candidato ficticio, en almacenamiento separado. Cierra el modo personal antes de abrir la demo en los mismos puertos. La demo nunca utiliza un proveedor de IA.

## Desarrollo y pruebas

![Python 3.12](https://img.shields.io/badge/Python-3.12-3776ab) ![JavaScript ES Modules](https://img.shields.io/badge/JavaScript-ES_Modules-f7df1e) ![FastAPI](https://img.shields.io/badge/FastAPI-009688) ![React 18](https://img.shields.io/badge/React-18-61dafb) ![TypeScript 5](https://img.shields.io/badge/TypeScript-5-3178c6) ![Vite 6](https://img.shields.io/badge/Vite-6-646cff)

La **release v0.1.0-alpha.2** superó **7.232 pruebas**, con seis omitidas:

| Suite | Superadas |
|---|---:|
| Career Agent: unitarias | 5.638 |
| Career Agent: integración | 1.282 |
| Career Agent: navegador | 266 |
| Resume Tailor: Python | 22 |
| Resume Tailor: frontend | 24 |

Estos resultados corresponden a la versión publicada, no a las ediciones posteriores del README. La [validación de la release](https://github.com/thais-stephanie/career-agent-portfolio/blob/v0.1.0-alpha.2/docs/VALIDATION.md) explica las omisiones, las comprobaciones estáticas, la instalación limpia en Windows y las exportaciones.

Consulta los comandos de desarrollo en [CONTRIBUTING.md](CONTRIBUTING.md). La interfaz compilada de Tailor está incluida; Node solo es necesario para reconstruirla. [Arquitectura](docs/ARCHITECTURE.md), [permisos de fuentes](docs/SOURCES.md) y [auditoría pública](docs/PUBLIC_AUDIT.md) explican las decisiones de ingeniería.

## Limitaciones
Alpha: las fuentes pueden cambiar, los lectores de idiomas son incompletos y la elegibilidad puede seguir siendo desconocida. Beta: sigues siendo responsable de revisar las evidencias; los módulos no comparten perfiles. PDF y el recuento real de páginas dependen de un renderizador local. La aplicación es para una sola persona, sin acceso remoto ni sincronización en la nube. La primera instalación no funciona sin internet.

Windows es la plataforma probada para la release. Se probó la conversión a PDF con Word; LibreOffice no estaba instalado. No se probó ningún proveedor de IA alojado con llamadas reales.

## Licencia y avisos de terceros
Career Agent utiliza [MIT](LICENSE). Resume Tailor, en `companion/resume-tailor`, utiliza [Apache-2.0](companion/resume-tailor/LICENSE). Las fuentes conservan sus licencias OFL. Consulta [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) para los límites entre componentes, dependencias y atribuciones.

Créditos a [Career-Ops](https://github.com/career-ops-hq/career-ops) por los patrones de protocolo que orientaron parte de los conectores. Referencias de presentación: [ECC](https://github.com/affaan-m/ECC), [Open Code Review](https://github.com/alibaba/open-code-review), [Ponytail](https://github.com/DietrichGebert/ponytail), [Colibri](https://github.com/JustVugg/colibri). Los avisos distinguen material adaptado, conocimiento de protocolo, inspiración y dependencias. No existe afiliación con esos proyectos.
