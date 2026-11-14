// Explore the demo graph yourself:
//   offgraph --store demo/offboarding.rl cypher "<one statement below>"

// 1. The shape of the graph: how many of each kind of node.
MATCH (n) RETURN labels(n)[0] AS kind, count(*) AS nodes ORDER BY nodes DESC;

// 2. Group nesting: every group and the chain of parents above it.
MATCH path = (g:Group)-[:CHILD_OF*0..6]->(top:Group)
WHERE NOT (top)-[:CHILD_OF]->()
RETURN g.name AS grp, [x IN nodes(path) | x.name] AS chain ORDER BY grp;

// 3. Who left, and how much they still cost each month.
MATCH (p:Person {status: 'left'})-[:HAS_ACCOUNT]->(a:Account {paid: true})-[:IN_APP]->(x:App)
RETURN p.email AS leaver, count(a) AS seats, sum(x.price) AS monthly_usd ORDER BY monthly_usd DESC;

// 4. Resources whose owner has left, with how many active people can still edit them through groups
//    (direct person grants are not counted here; `offgraph orphans` does count them).
MATCH (o:Person {status: 'left'})-[:OWNS]->(r:Resource)
OPTIONAL MATCH (e:Person {status: 'active'})-[:MEMBER_OF]->(:Group)-[:CHILD_OF*0..6]->(:Group)-[c:CAN]->(r)
WHERE c.access IN ['edit', 'admin']
RETURN r.id AS resource, r.title AS title, o.email AS owner, count(DISTINCT e) AS active_editors ORDER BY resource;

// 5. Groups with no active admin at all (already orphaned today).
MATCH (g:Group)
OPTIONAL MATCH (a:Person {status: 'active'})-[:MEMBER_OF {role: 'admin'}]->(g)
WITH g, count(a) AS admins WHERE admins = 0
RETURN g.name AS grp ORDER BY grp;
