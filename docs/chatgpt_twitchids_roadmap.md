# TwitchIDS Development Roadmap

## 1. High‑Impact Improvements to the Current UI

### Threat Classification Panel

Instead of only displaying "No active threats", classify detected
events.

Example: - Spam Burst - Copy‑Paste Flood - Bot Cluster - Raid - Link
Spam - Command Spam

Display fields such as: - Threat Type - Confidence Score - Users
involved - Pattern detected

------------------------------------------------------------------------

### Suspicious User List

Add a panel that lists users flagged by the detection engine.

Example fields: - Username - Suspicion score - Duplicate ratio - Cluster
membership - Account age

Clicking a user should open a **User Inspector Panel** showing: -
Message count - Join time - Cluster ID - Reputation score - Behavioral
signals

------------------------------------------------------------------------

### Chat Activity Heatmap

Visualize chat intensity over time.

Benefits: - Identify raids - Detect spam bursts - Understand hype spikes

Possible displays: - time vs message volume heatmap - minute‑by‑minute
activity blocks

------------------------------------------------------------------------

### Event Timeline

Create a chronological log of system events.

Example: - Velocity spike detected - Duplicate cluster detected - Bot
cluster identified - Threat classification triggered

Purpose: - Post‑stream analysis - Incident review - Moderation reporting

------------------------------------------------------------------------

### Signal Confidence Display

Instead of binary signals, show weighted values.

Example:

Velocity Spike: 8 / 10\
Duplicate Flood: 6 / 10\
Semantic Cluster: 2 / 10

This improves transparency and trust in the scoring model.

------------------------------------------------------------------------

## 2. Features That Could Turn This Into a Full Moderation Platform

### Bot Cluster Visualization

Graph suspicious accounts based on shared behaviors:

Signals used: - identical messages - synchronized timing - similar
usernames - join time proximity

Graph nodes = users\
Graph edges = shared signals

Clusters represent coordinated bot groups.

------------------------------------------------------------------------

### Raid Early Warning System

Detect coordinated raids before spam begins.

Signals: - join rate spike - new account surge - follower spike -
abnormal account age distribution

Alert example: "Possible raid forming: 42 users joined within 5
seconds."

------------------------------------------------------------------------

### Automated Moderation Suggestions

Instead of automatic moderation actions, provide suggestions to
moderators.

Example recommendations: - enable slow mode - timeout suspicious
cluster - enable follower‑only mode

Purpose: Keep moderators in control while providing decision support.

------------------------------------------------------------------------

### Cross‑Stream Bot Network Tracking

Track suspicious accounts across multiple monitored streams.

Example insight: User appears in several channels performing similar
actions.

This helps identify coordinated bot farms operating across Twitch.

------------------------------------------------------------------------

### Spam Pattern Library

Maintain a database of known spam campaigns.

Examples: - crypto scams - fake giveaways - follower bot promotions -
adult link spam

When a pattern is matched: - display pattern name - show confidence
level - highlight matching messages

------------------------------------------------------------------------

## 3. Detection Techniques Bot Farms Struggle to Evade

### 1. Temporal Synchronization Detection

Bots frequently operate on shared timers.

Detection method: Identify clusters of messages posted within extremely
small time windows.

Indicators: - many users posting within milliseconds - repeated
synchronized message bursts

Humans naturally produce asynchronous chat timing.

------------------------------------------------------------------------

### 2. Duplicate Message Similarity

Bots often reuse message templates.

Detection methods: - Levenshtein distance - cosine similarity - fuzzy
matching

If similarity \> threshold, messages are clustered as spam.

------------------------------------------------------------------------

### 3. Username Entropy Analysis

Bot usernames often follow predictable patterns.

Indicators: - high numeric density - repeating prefixes - generated
character sequences

Compute Shannon entropy and digit ratios to detect automated name
generation.

------------------------------------------------------------------------

### 4. Join Burst Detection

Bot attacks frequently involve batches of accounts joining
simultaneously.

Signals: - join rate spikes - unusually young account ages - multiple
joins within a few seconds

Join velocity exceeding baseline can indicate raids or bot attacks.

------------------------------------------------------------------------

### 5. Conversation Context Failure

Bots usually do not participate in natural conversation flow.

Detection method: Compare message semantic similarity with recent chat
context.

Indicators: - messages unrelated to ongoing discussion - repetitive
promotion or link spam - lack of conversational engagement

Low contextual similarity suggests automated behavior.

------------------------------------------------------------------------

## Recommended Detection Strategy

Use a **multi‑signal scoring model** combining all signals:

Example scoring model:

Temporal Synchronization → +20\
Duplicate Messages → +15\
Username Entropy → +10\
Join Burst → +20\
Context Failure → +15

Total score determines risk level:

-   Score \> 40 → Suspicious
-   Score \> 70 → Likely Bot

Combining signals dramatically improves detection reliability and makes
evasion significantly harder.
