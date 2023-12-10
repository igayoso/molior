.EXPORT_ALL_VARIABLES:
.PHONY: MAKECMDGOALS

export COMPOSE_PROJECT_NAME=molior
export DOCKER_GROUP_ID=$(shell getent group docker | cut -d: -f3)

start:  ## run containers (background)
	@docker-compose --profile serve up -d

# Self-documenting Makefile
# https://marmelab.com/blog/2016/02/29/auto-documented-makefile.html
help:  ## Print this help
	@grep -E '^[a-zA-Z][a-zA-Z0-9_-]*:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-30s\033[0m %s\n", $$1, $$2}'

docker-compose-build:  ## build containers
	docker-compose build --no-cache

docker-compose-build-cached:  ## build containers (cached)
	docker-compose build --build-arg UID=$(shell id -u $$USER)

api:
	docker-compose build --no-cache api

api-cached:
	docker-compose build api

web:
	docker-compose build --no-cache web

web-cached:
	docker-compose build web

aptly:
	docker-compose build --no-cache aptly

aptly-cached:
	docker-compose build aptly

postgres:
	docker-compose build --no-cache postgres

nginx:
	docker-compose build --no-cache nginx

registry:
	docker-compose build --no-cache registry

stop:  ## stop containers
	@docker-compose --profile serve --profile test down

stop-api:  ## stop api container
	@docker-compose stop api

stop-nginx:  ## stop nginx container
	@docker-compose stop nginx

stop-web:  ## stop web container
	@docker-compose stop web

clean:  ## clean containers and volumes
	@echo; echo "This will delete volumes and data!"; echo Press Enter to continue, Ctrl-C to abort ...; read x
	docker-compose --profile serve --profile test down -v

remove: clean   ## remove containers and volumes
	docker rmi -f molior_web:latest molior_api:latest molior_postgres:latest molior_aptly:latest molior_nginx:latest molior_registry:latest

logs:  ## show logs
	@docker-compose logs -f api postgres web aptly nginx

logs-api:  ## show api logs
	@docker-compose logs -f api

logs-registry:  ## show registry logs
	@docker-compose logs -f registry

logs-web:  ## show web logs
	@docker-compose logs -f web

logs-postgres:  ## show postgres logs
	@docker-compose logs -f postgres

shell-api:  ## login to api container
	docker-compose exec api /bin/bash

shell-postgres:  ## login to postgres container
	docker-compose exec postgres /bin/bash

shell-aptly:  ## login to aptly container
	docker-compose exec aptly /bin/bash

shell-nginx:  ## login to nginx container
	docker-compose exec nginx /bin/bash

shell-registry:  ## login to registry container
	docker-compose exec registry /bin/bash

psql:  ## login to api container
	docker-compose exec postgres su postgres -c "psql molior"
