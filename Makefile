# Конвейер объединения сработок: все команды в Docker, мусора на хосте нет.
COMPOSE = docker compose
RUN = $(COMPOSE) run --rm pipeline

.PHONY: build ingest info run run-smoke bench shell clean

build:            ## собрать образ (веса YOLO зашиваются на этапе сборки)
	$(COMPOSE) build pipeline

ingest:           ## индексация выгрузки (inputs/ по умолчанию)
	$(RUN) ingest

info:             ## сводка по индексу
	$(RUN) info

run:              ## полный прогон конвейера по всему индексу
	$(RUN) run

run-smoke:        ## быстрый смоук без детектора (только деградация+сессии)
	$(RUN) run --no-detector --limit 500

bench:            ## замер латентности стадий 0.5-1 (CPU-ориентир)
	$(RUN) bench

shell:            ## интерактивный шелл в контейнере
	$(COMPOSE) run --rm --entrypoint bash pipeline

clean:            ## удалить образ и контейнеры (runs/ остаётся)
	$(COMPOSE) down --rmi local --remove-orphans
