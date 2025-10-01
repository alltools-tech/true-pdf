---

## 8) `Makefile`
```makefile
up:
	docker-compose up --build

down:
	docker-compose down

logs:
	docker-compose logs -f

bash:
	docker-compose exec app bash

reload:
	docker-compose down && docker-compose up --build
