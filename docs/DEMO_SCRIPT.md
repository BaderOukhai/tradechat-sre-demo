# Script de démo (3 minutes)

> Objectif : montrer les deux promesses — **messagerie temps réel fiable et conforme** (rien
> perdu, tout archivé) et **opérée comme une équipe SRE** (SLO, alertes, runbooks, incident,
> rollback, post-mortem) — puis ouvrir la discussion.

## Avant la démo (10 min)

```bash
make cluster images monitoring deploy      # ~3 min sur une machine où les images existent
make load ARGS="--duration 3600"           # dans un terminal à part : 1 h de trafic
```

Onglets à ouvrir, dans cet ordre :
1. Grafana — `http://localhost:3000/d/messaging-slo` (accès anonyme, lecture seule)
2. Alertmanager — `http://localhost:9093`
3. Un terminal à la racine du repo, police agrandie
4. (option) Claude Code à la racine du repo, serveur MCP `tradechat-ops` approuvé

**2 à 3 minutes avant de partager l'écran** : `make bad-release`. L'alerte met ~2 min 30 à passer
en *firing* (`for: 2m`) : elle sera rouge au moment où tu arrives à l'incident.

**Plan B** (Wi-Fi, Docker, démo qui plante) : `docs/assets/` — captures du dashboard au repos, en
incident, Alertmanager, et le GIF de la séquence incident → rollback.

---

## 0:00 – 0:15 · Contexte

> « J'ai voulu reproduire en miniature ce qui fait votre valeur : une messagerie temps réel pour des
> traders, où **aucun message ne doit être perdu** parce que les banques ont l'obligation légale de
> tout archiver — et l'opérer comme le ferait une équipe SRE. Tout tourne en local, sur un cluster
> Kubernetes de 3 nœuds, coût cloud nul. »

## 0:15 – 0:45 · Le schéma (README, diagramme)

> « Un trader poste un message. La **chat-api** — 3 réplicas répartis sur les nœuds — l'écrit
> d'abord dans un **Redis Stream** persistant, et seulement ensuite répond 202 : un message accepté
> est un message durable. Chaque réplica lit le Stream et pousse le message à ses clients WebSocket.
> En parallèle, l'**archiver** lit le même Stream via un **consumer group** : il écrit sur disque,
> fait un fsync, et seulement après il acquitte. Garantie **at-least-once** : jamais de perte,
> au pire un doublon dédupliqué par l'id. Prometheus scrape tout, Grafana affiche le SLO,
> Alertmanager porte trois alertes, chacune avec son runbook. »

## 0:45 – 1:30 · Grafana en direct

Montrer, de gauche à droite et de haut en bas :
- **SLO** : « 99,9 % des messages livrés en moins de 200 ms. Le SLI, c'est le bucket 0,2 de
  l'histogram divisé par le total. Le budget d'erreur, c'est les 0,1 % restants. »
- **Écart de conformité** : « Messages acceptés mais pas encore archivés, mesuré à la source dans
  Redis — lag plus pending du consumer group. Il doit rester à 0. J'avais d'abord fait
  *reçus moins archivés* avec deux compteurs : ça donnait une fausse alerte à chaque redémarrage
  de pod, parce qu'un compteur repart à zéro. »
- **Latence p50/p95/p99** et **saturation** : « Pas de limite CPU sur la chat-api : le throttling
  dégraderait la latence, donc le SLO. »

> Si on a le temps : `kubectl delete pod <un pod chat-api>` → petit creux sur *Ready pods*, aucune
> erreur HTTP, rien de perdu. (Mesuré : 2 399 / 2 399 messages archivés, p99 11,9 ms.)

## 1:30 – 2:30 · L'incident : détecter, restaurer, comprendre

- Grafana : **p99 à ~500 ms**, ligne rouge des 200 ms franchie, budget qui fond.
- Alertmanager : **`HighMessageLatencyP99` en firing**, avec le lien vers le runbook.
- (option) Claude Code : *« Is the messaging platform healthy? What should I do? »* → l'agent MCP
  lit les alertes, le SLO, l'écart de conformité, et cite le runbook. « Outils en lecture seule,
  chaque appel journalisé : c'est l'humain qui exécute. »

```bash
helm history tradechat     # la révision fautive est juste là
make rollback              # helm rollback : rolling update vers la version précédente
```

> « On restaure d'abord, on comprend ensuite. Le rollback est une nouvelle révision, pas un
> retour en arrière silencieux : l'historique reste auditable. Trente secondes plus tard la
> p99 redescend, l'alerte se résout, et j'écris le post-mortem — sans blâme. »

Montrer `docs/postmortems/` (chronologie réelle, time to detect / time to mitigate, actions).

## 2:30 – 3:00 · Ce qui manque pour la production, et ce que j'ai appris

> « Pour la production : mTLS entre services, secrets dans un KMS ou Vault, Redis répliqué ou
> Kafka, multi-région, chiffrement de bout en bout avec des clés détenues par le client, archive
> immuable en WORM, et des SLO par client. Ce que j'ai appris : mesurer à la source plutôt qu'à
> partir de compteurs qui se remettent à zéro, toujours déclencher ses alertes volontairement
> pour les tester — j'ai trouvé deux bugs comme ça — et le fait qu'une correction manuelle
> n'est pas terminée tant qu'elle n'est pas dans Git. »

> « Je l'ai construit avec un assistant de code IA, Claude Code, en vérifiant et en comprenant
> chaque décision — c'est documenté dans le README. Posez-moi des questions sur n'importe quelle
> ligne. »

---

## Questions fréquentes

| Question | Une phrase |
|---|---|
| Pourquoi 3 réplicas ? | Perdre un pod (crash, rolling update, drain) en laisse 2 en service ; 2 workers → répartition 2+1 garantie par le topology spread. |
| Et si Redis meurt ? | Les POST répondent 503 (le client réessaie, rien de perdu en silence), les pods sortent du trafic (readiness), Redis redémarre et relit son AOF sur le volume. Point unique de panne assumé : en prod, Redis répliqué ou Kafka. |
| Pourquoi ACK après l'écriture ? | ACK avant = perte possible, ACK après = doublon possible : en conformité, perdre est illégal, dupliquer est gênant. |
| Pourquoi 200 ms ? | Seuil où le trader perçoit le retard ; mesuré à ~5 ms en temps normal, donc une marge × 40 : l'alerte ne se déclenche que sur une vraie régression. |
| Passer à 1 400 institutions ? | Kafka partitionné par client/room, chat-api sans état derrière un load balancer + HPA, archiver scalé par partition, SLO et quotas par client, cellules par région. |
