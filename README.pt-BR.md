[English](README.md) | [Português](README.pt-BR.md) | [Español](README.es.md)

# Career Agent

Encontre vagas, entenda como combinam com sua busca e adapte seu currículo com experiências que você pode comprovar.

![Career Agent](docs/assets/readme/hero.png)

*Ilustração do produto com exemplos inventados, não uma captura de tela nem resultados medidos. Veja as capturas reais da demo abaixo.*

[![Alpha 2](https://img.shields.io/badge/status-Alpha_2-fff08a)](https://github.com/thais-stephanie/career-agent-portfolio/releases/tag/v0.1.0-alpha.2) [![Resume Tailor Beta](https://img.shields.io/badge/Resume_Tailor-Beta-d8c8ff)](#resume-tailor-beta) [![Alpha 2 tests](https://img.shields.io/badge/Alpha_2_tests-7%2C232_passed-a7ebcf)](https://github.com/thais-stephanie/career-agent-portfolio/blob/v0.1.0-alpha.2/docs/VALIDATION.md) [![Windows validated for Alpha 2](https://img.shields.io/badge/Alpha_2-Windows_validated-bbd6ff)](https://github.com/thais-stephanie/career-agent-portfolio/releases/tag/v0.1.0-alpha.2)

**[Baixar para Windows](https://github.com/thais-stephanie/career-agent-portfolio/releases/tag/v0.1.0-alpha.2) · [Experimentar a demo](#demo) · [Ver o aplicativo](#tour-visual)**

## Status do projeto
**Alpha v0.1.0-alpha.2**, com **Resume Tailor Beta** incluído. É um aplicativo local funcional, ainda em desenvolvimento. Search Fit explica suas preferências de busca; não representa probabilidade de contratação.

## Instalação mais fácil: Windows

1. Abra [Releases](https://github.com/thais-stephanie/career-agent-portfolio/releases/tag/v0.1.0-alpha.2).
2. Baixe `Career-Agent-v0.1.0-alpha.2-Windows.zip`.
3. Clique com o botão direito no ZIP, escolha **Extrair Tudo** e abra a pasta extraída.
4. Dê dois cliques em **Start-Career-Agent.cmd**. Deixe a janela aberta.
5. Aguarde a preparação. O navegador abrirá automaticamente.

Você não precisa instalar Python nem Node. O inicializador baixa o uv, uma ferramenta que gerencia Python e pacotes, quando necessário; o uv instala Python 3.12 e as dependências nas versões registradas. A primeira instalação precisa de internet. Use uma pasta em que você possa gravar arquivos, fora de Arquivos de Programas. [Ajuda para começar](FIRST_RUN.md).

## Como abrir novamente
Dê dois cliques no mesmo launcher, na mesma pasta. Seus dados ficam ali. Pressione **Ctrl+C** na janela para encerrar os dois aplicativos. Faça backup antes de mudar de versão.

## O que ele faz

| Etapa | O que você encontra |
|---|---|
| Descobrir vagas | Mais de 25 conectores para sistemas de empregadores, sites de vagas remotas e agregadores, além da importação manual. Disponibilidade e configuração variam por fonte. |
| Verificar elegibilidade | Restrições de contratação ficam separadas das preferências. “Remoto” não significa contratação mundial; o que falta confirmar continua em aberto. |
| Entender o Search Fit | Uma pontuação transparente que compara fatos da vaga com suas preferências de busca, mostrando motivos e informações ausentes. Não é probabilidade de contratação. |
| Revisar Career Evidence | Extraia sugestões do currículo e confirme, edite ou rejeite cada uma. Importar texto não confirma uma experiência. |
| Acompanhar | Guarde o status e as notas das candidaturas que você decide enviar. |

## Resume Tailor Beta
Abra **Resume Tailor Beta** no menu lateral. Dentro de uma vaga, escolha **Copy job description**, abra o Tailor e cole em **Tailor resume**.

Crie um perfil de candidato, adicione currículo-base e fontes, revise evidências, analise a vaga, examine correspondências e lacunas, gere, edite, valide e exporte. Markdown e Word funcionam sem IA. PDF requer Microsoft Word ou LibreOffice instalado no computador; quando não há nenhum deles, o aplicativo explica a indisponibilidade.

Os módulos guardam evidências separadamente. Perfil e evidências não são sincronizados automaticamente. **Search Fit e Tailor Match respondem a perguntas diferentes.** Texto gerado nunca vira Career Evidence confirmada. Experiência sem respaldo não deve virar afirmação no currículo. Consulte as [garantias de honestidade](docs/ARCHITECTURE.md).

## Tour visual

![Fluxo: descobrir vagas, examinar Search Fit e motivos, preparar com Tailor Beta e acompanhar candidaturas](docs/assets/readme/how-it-works.png)

*Ilustração do fluxo. As capturas abaixo mostram o aplicativo real com dados fictícios da demonstração.*

| Descobrir vagas | Entender “Why this matches” |
|---|---|
| ![Discover com vagas fictícias](docs/assets/readme/discover.png) | ![Motivos do Search Fit e informações ausentes](docs/assets/readme/why.png) |

| Preparar com Resume Tailor Beta | Começar seu espaço de trabalho |
|---|---|
| ![Resume Tailor Beta](docs/assets/readme/tailor.png) | ![Configuração do primeiro uso](docs/assets/readme/first-run.png) |

## Privacidade
Configurações, vagas, notas e evidências ficam armazenadas localmente. O Tailor mantém os documentos enviados como fontes; o Career Agent extrai texto do CV sem guardar o arquivo enviado. Provedores opcionais de IA podem receber conteúdo quando você os configura e utiliza. Coleta de vagas e links de empregadores também usam internet. Não há telemetria implementada. Leia o [modelo de privacidade](docs/PRIVACY.md), incluindo backups e área de transferência.

## O que ele deliberadamente não faz
Não envia candidaturas automaticamente, não promete entrevistas, não interpreta trabalho remoto como elegibilidade mundial, não esconde lacunas nem trata texto de IA como experiência confirmada.

## Instalação pelo terminal

<details>
<summary>Configuração manual para desenvolvedores</summary>

**Windows: abra Iniciar, digite PowerShell e abra Windows PowerShell.** No macOS, abra Aplicativos → Utilitários → Terminal. No Linux, abra o aplicativo Terminal.

Instale o [uv](https://docs.astral.sh/uv/getting-started/installation/), baixe e extraia o ZIP do código-fonte em [Releases](https://github.com/thais-stephanie/career-agent-portfolio/releases/tag/v0.1.0-alpha.2). Digite `cd` seguido do caminho da pasta entre aspas. Execute:

```powershell
uv sync --locked --python 3.12
uv run python scripts/launch.py
```

Não é necessário instalar Python manualmente. Windows é a plataforma testada para este release. Para outras portas: `uv run python scripts/launch.py --port 8875` (o Tailor usará 8876).

</details>

## Demo
Dê dois cliques em **Start-Demo.cmd**, ou execute `uv run python scripts/launch.py --demo`. A demonstração cria vagas inventadas e Alex Morgan, um candidato fictício, em armazenamento separado. Encerre o modo pessoal antes de abrir a demo nas mesmas portas. A demo nunca usa provedor de IA.

## Desenvolvimento e testes

![Python 3.12](https://img.shields.io/badge/Python-3.12-3776ab) ![JavaScript ES Modules](https://img.shields.io/badge/JavaScript-ES_Modules-f7df1e) ![FastAPI](https://img.shields.io/badge/FastAPI-009688) ![React 18](https://img.shields.io/badge/React-18-61dafb) ![TypeScript 5](https://img.shields.io/badge/TypeScript-5-3178c6) ![Vite 6](https://img.shields.io/badge/Vite-6-646cff)

A **release v0.1.0-alpha.2** teve **7.232 testes aprovados**, com seis ignorados:

| Suíte | Aprovados |
|---|---:|
| Career Agent: unitários | 5.638 |
| Career Agent: integração | 1.282 |
| Career Agent: navegador | 266 |
| Resume Tailor: Python | 22 |
| Resume Tailor: frontend | 24 |

Esses resultados pertencem à build publicada, não às edições posteriores do README. A [validação da release](https://github.com/thais-stephanie/career-agent-portfolio/blob/v0.1.0-alpha.2/docs/VALIDATION.md) explica os testes ignorados, as verificações estáticas, a instalação limpa no Windows e as exportações.

Veja os comandos de desenvolvimento em [CONTRIBUTING.md](CONTRIBUTING.md). A interface compilada do Tailor está incluída; Node só é necessário para reconstruí-la. [Arquitetura](docs/ARCHITECTURE.md), [permissões das fontes](docs/SOURCES.md) e [auditoria pública](docs/PUBLIC_AUDIT.md) explicam as decisões de engenharia.

## Limitações
Alpha: fontes podem mudar, os leitores de idioma são incompletos e a elegibilidade pode continuar desconhecida. Beta: você continua responsável pela revisão das evidências; os módulos não compartilham perfis. PDF e contagem real de páginas dependem de um renderizador local. O app atende uma pessoa por vez, sem acesso remoto nem sincronização em nuvem. A primeira instalação não funciona offline.

Windows é a plataforma testada para a release. A conversão para PDF com Word foi testada; LibreOffice não estava instalado. Nenhum provedor de IA hospedado foi testado ao vivo.

## Licença e avisos de terceiros
Career Agent usa [MIT](LICENSE). Resume Tailor, em `companion/resume-tailor`, usa [Apache-2.0](companion/resume-tailor/LICENSE). As fontes mantêm suas licenças OFL. Veja [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) para os limites entre componentes, dependências e atribuições.

Créditos ao [Career-Ops](https://github.com/career-ops-hq/career-ops) pelos padrões de protocolo que orientaram parte dos conectores. Referências de apresentação: [ECC](https://github.com/affaan-m/ECC), [Open Code Review](https://github.com/alibaba/open-code-review), [Ponytail](https://github.com/DietrichGebert/ponytail), [Colibri](https://github.com/JustVugg/colibri). Os avisos distinguem material adaptado, conhecimento de protocolo, inspiração e dependências. Não há vínculo com esses projetos.
