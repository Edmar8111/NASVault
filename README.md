## Projeto - NASVault

[![Active Development](https://img.shields.io/badge/Maintenance%20Level-Actively%20Developed-brightgreen.svg)](https://gist.github.com/cheerfulstoic/d107229326a01ff0f333a1d3476e068d)
![status](https://img.shields.io/badge/status-stable-green.svg) ![pyversions](https://img.shields.io/badge/python-3.10%2B-blue)

[![Python Badge](https://img.shields.io/badge/-Python-22272e?style=for-the-badge&logo=python&logoColor=3776AB)](https://www.python.org/)
[![FastAPI Badge](https://img.shields.io/badge/-FastAPI-22272e?style=for-the-badge&logo=fastapi&logoColor=009688)](https://fastapi.tiangolo.com/)
[![MySQL Badge](https://img.shields.io/badge/-MySQL-22272e?style=for-the-badge&logo=mysql&logoColor=4479A1)](https://www.mysql.com/)
[![Docker Badge](https://img.shields.io/badge/-Docker-22272e?style=for-the-badge&logo=docker&logoColor=2496ED)](https://www.docker.com/)
[![Linux Badge](https://img.shields.io/badge/-Linux-22272e?style=for-the-badge&logo=linux&logoColor=FCC624)](https://www.kernel.org/doc/html/latest/)
[![Jinja Badge](https://img.shields.io/badge/-Jinja2-22272e?style=for-the-badge&logo=jinja&logoColor=B41717)](https://palletsprojects.com/p/jinja/)

## Sumário
- [Introdução](#introdução)
- [Tecnologias](#tecnologias)
- [Como rodar](#como-rodar)

## Introdução
Esse repositório contém uma API REST para armazenamento e gerenciamento de arquivos, com foco em upload, autenticação e visualização de conteúdo por usuário. A aplicação utiliza Python e FastAPI para expor endpoints REST, além de uma interface web renderizada com Jinja2.

O projeto foi estruturado para permitir que usuários criem contas, enviem arquivos, acompanhem o processamento de uploads, consultem itens armazenados e realizem download do conteúdo salvo.

## Tecnologias
- Python 3.10+
- FastAPI
- Uvicorn
- Jinja2
- MySQL + PyMySQL
- JWT (PyJWT)
- Docker + Docker Compose
- Python Multipart
- Cryptography
- Linux / WSL

## Como rodar
A maneira indicada de executar esse projeto é utilizando os containers da aplicação. Após clonar o repositório, no diretório raiz execute o comando abaixo:

```bash
docker compose up -d --build
```

Esse comando irá criar e subir os serviços necessários para a API e o banco de dados. Após a inicialização, a aplicação estará disponível em:

- API: http://localhost:8001
- Documentação Swagger: http://localhost:8001/docs

O projeto também pode ser executado localmente com Python, mas o uso do Docker é o fluxo recomendado para manter a configuração do ambiente consistente.

---

## Project - NASVault

## Table of Contents
- [Introduction](#introduction)
- [Technologies](#technologies)
- [Running the Project](#running-the-project)

## Introduction
This repository contains a REST API for storing and managing files, focused on uploads, authentication, and per-user content browsing. The application uses Python and FastAPI to expose REST endpoints, along with a web interface rendered with Jinja2.

The project is designed to allow users to create accounts, upload files, track upload processing, browse stored items, and download saved content.

## Technologies
- Python 3.10+
- FastAPI
- Uvicorn
- Jinja2
- MySQL + PyMySQL
- JWT (PyJWT)
- Docker + Docker Compose
- Python Multipart
- Cryptography
- Linux / WSL

## Running the Project
The recommended way to run this project is by using the application containers. After cloning the repository, run the following command from the root directory:

```bash
docker compose up -d --build
```

This command creates and starts the services required by the API and database. Once they have started, the application is available at:

- API: http://localhost:8001
- Swagger documentation: http://localhost:8001/docs

The project can also be run locally with Python, but Docker is the recommended approach for keeping the environment configuration consistent.
