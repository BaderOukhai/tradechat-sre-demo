# tradechat-sre-demo

[![ci](https://github.com/BaderOukhai/tradechat-sre-demo/actions/workflows/ci.yml/badge.svg)](https://github.com/BaderOukhai/tradechat-sre-demo/actions/workflows/ci.yml) · 🇬🇧 [English version](README.md) · **[L'étude de cas en une minute →](https://baderoukhai.com/tradechat)**

> **Des traders échangent en temps réel, et la loi impose d'archiver chaque message.**
> Le système fait donc deux promesses : *rapide*, et *rien de perdu*.
> J'en ai construit une miniature sur Kubernetes — puis j'ai passé autant de temps à essayer de la casser.

![Une mauvaise release part en production : la p99 passe de 5 ms à environ 500 ms, l'alerte se déclenche, une commande annule la release](docs/assets/incident.gif)

*Une mauvaise release part en production : la latence p99 passe de 5 ms à ~500 ms, l'alerte se
déclenche, une seule commande l'annule — **161 s pour détecter, 79 s pour rétablir, 0 message
perdu** ([post-mortem, en anglais](docs/postmortems/2026-10-25-latency-regression.md)).*

## Acte 1 — Construire : rapide, et rien de perdu

- Un message est écrit dans un journal durable (un Redis Stream sur un volume persistant) **avant**
  que le trader reçoive `202 Accepted`.
- Trois réplicas de l'API, répartis sur deux nœuds, le poussent à chaque trader connecté en
  WebSocket : **p99 ≈ 12–17 ms** sous charge, pour un SLO de 99,9 % sous 200 ms.
- Un archiveur lit le même journal via un consumer group et n'acquitte un message qu'**après**
  l'avoir écrit sur disque : un crash peut dupliquer un message, jamais le perdre.
- Chaque commit passe par un pipeline qui cherche les secrets et les vulnérabilités, **déploie sur
  un cluster Kubernetes jetable, lance 20 traders simulés, et rejette la release si le SLO n'est
  pas tenu ou si un seul message n'est pas archivé** — le verdict est sur la
  [page du run](https://github.com/BaderOukhai/tradechat-sre-demo/actions/workflows/ci.yml).

## Acte 2 — Le casser exprès

| J'ai… | Le système a… | Mesuré |
|---|---|---|
| tué un pod de l'API sous charge | contourné le pod | 0 erreur HTTP, 2 399 / 2 399 messages archivés ([dashboard](docs/assets/dashboard-resilience.png)) |
| arrêté l'archiveur | gardé les messages, alerté (critique), rattrapé son retard | alerte après 75 s, 1 249 messages rattrapés en < 15 s ([dashboard](docs/assets/dashboard-compliance.png)) |
| livré une release 300 ms plus lente | alerté, été restauré en une commande | détection 161 s, rétablissement 79 s, 0 perte ([dashboard](docs/assets/dashboard-incident.png)) |
| livré une release qui manque de mémoire | refusé de la déployer | les anciens pods ont continué à servir, aucun impact |
| confié les déploiements au GitOps (Argo CD) | annulé tout seul ma modification manuelle | dérive annulée en < 5 s, archive conservée |
| demandé à un assistant IA *« la plateforme va-t-elle bien ? »* | répondu à partir des alertes et des runbooks — en lecture seule, journalisé | chaque appel dans un journal d'audit |

## Acte 3 — Ce que le casser m'a appris

Le casser exprès a révélé sept vrais problèmes qu'une suite de tests verte ne voyait pas :

1. **Un redémarrage de Redis laissait une connexion morte** dans le pool : la requête suivante
   échouait → reconnexion et nouvel essai.
2. **L'indicateur de conformité criait au loup à chaque redémarrage de pod** (compteurs remis à 0)
   → mesurer à la source, dans Redis.
3. **`helm upgrade` annulait en silence une correction manuelle** → une correction faite pendant un
   incident n'est pas terminée tant qu'elle n'est pas dans Git.
4. **Une alerte continuait de sonner 5 minutes après la correction** → fenêtre plus courte.
5. **Les mises à jour progressives coupaient des requêtes en cours** → un délai `preStop` de 5 s → 0 erreur.
6. **Grafana manquait de mémoire pendant l'incident qu'il affichait** → dimensionné à partir de l'observation.
7. **Un kubeconfig contenant la clé privée du cluster s'est glissé dans un commit** → intercepté
   avant le push, puis automatisé : détection de secrets en pre-commit et en CI, vérifiée sur ce
   fichier précis.

**Des preuves, pas des promesses :** [runs de CI avec le rapport de déploiement](https://github.com/BaderOukhai/tradechat-sre-demo/actions/workflows/ci.yml) ·
[post-mortem](docs/postmortems/2026-10-25-latency-regression.md) · [runbooks](docs/runbooks/) ·
[conception de la CI](docs/ci.md)

---

Les détails techniques — architecture, installation, SLO, alertes, décisions et compromis — sont
dans le [README en anglais](README.md#under-the-hood), la langue de travail du projet.
