# Git Graph Commit Ordering

## The Problem

We want to display commits in a 2D grid where:
- **Y-axis**: Time (newer commits at top, older at bottom)
- **X-axis**: Different branches/lines of development

Constraints:
1. **Time consistency across columns**: If commit A is at row 5 and commit B is at row 3, then A must be older than B (or at least not newer)
2. **Parent-child relationships**: A parent must always be below its children
3. **Minimize wasted space**: Don't leave unnecessary gaps

## Key Insight: Topological Sort with Time Tie-Breaking

The y-position isn't literally "time" - it's a **topological ordering** where time is used as a tie-breaker.

Why? Because:
- Parent must always be below child (topological constraint)
- When two commits have no ancestor relationship, use time to decide relative position

## The Algorithm

### Step 1: Topological Sort with Time

Assign each commit a "row" based on:
1. All parents must have higher row numbers (further down)
2. Among commits that could go in the same position, order by time

This is essentially: **reverse topological sort, stable-sorted by commit time**

### Step 2: Assign Columns (X positions)

This is about tracking "lanes" for different lines of development:
- When a branch starts (commit has no children in our view), it needs a column
- When branches merge, we can potentially free up a column
- We want to minimize crossings and keep related commits together

### Formalization

Let's define:
- `row(c)` = y-position of commit c
- `time(c)` = commit timestamp
- `parents(c)` = set of parent commits

**Constraint 1 (Topological)**: For all commits c and all p in parents(c):
```
row(c) < row(p)
```

**Constraint 2 (Time Consistency)**: For commits a, b with no ancestor relationship:
```
if time(a) > time(b) then row(a) ≤ row(b)
```

**Objective**: Minimize total rows used (or equivalently, minimize gaps)

### Example

```
Time    Commits
----    -------
T5      A (branch-x)
T4      B (branch-y)  
T3      C (branch-x, parent of A)
T2      D (branch-y, parent of B)
T1      E (merge base, parent of C and D)
```

Naive approach (pure time ordering):
```
Row 0: A
Row 1: B
Row 2: C
Row 3: D
Row 4: E
```

But this wastes space! A and B can share a row since they're independent:

```
Row 0: A   B
Row 1: C   D
Row 2:   E
```

### The Compaction Algorithm

1. **Compute topological depth**: For each commit, its minimum possible row is `1 + max(depth of children)` (or 0 if no children)

2. **Process commits in time order** (newest first):
   - Assign to the earliest row that satisfies:
     a. Row ≥ topological minimum
     b. Column is available at that row
     c. Column assignment is consistent with parent/child relationships

Wait, let me reconsider. The issue is that we're conflating two different things:
- Row assignment (y-position)
- Column assignment (x-position)

Let me separate these clearly.

## Revised Approach

### Phase 1: Row Assignment

**Goal**: Assign each commit a row such that:
1. Parents are always in higher-numbered rows than children
2. We use as few rows as possible
3. Time ordering is respected where possible

**Algorithm**:

```python
def assign_rows(commits):
    # Sort by time, newest first
    sorted_commits = sorted(commits, key=lambda c: -c.time)
    
    # For each commit, track the minimum row it can be in
    # (must be > all children's rows)
    min_row = {}
    row = {}
    
    for commit in sorted_commits:
        # Find minimum row based on children
        children = get_children(commit)
        if children:
            min_possible = max(row[child] for child in children) + 1
        else:
            min_possible = 0
        
        min_row[commit] = min_possible
        row[commit] = min_possible  # Greedy: use minimum possible
    
    return row
```

Wait, this doesn't work because we process newest first, so we don't know children's rows yet.

Let me flip it:

```python
def assign_rows(commits):
    # Sort by time, oldest first
    sorted_commits = sorted(commits, key=lambda c: c.time)
    
    row = {}
    max_row = -1
    
    # Process oldest first, assign from bottom up
    for commit in sorted_commits:
        parents = commit.parents
        if parents:
            # Must be above all parents
            min_possible = max(row[parent] for parent in parents if parent in row) + 1
        else:
            # Root commit - can go at row 0
            min_possible = 0
        
        # But we want to go as low as possible to save space
        # Actually, if we're going oldest-first, we're building bottom-up
        # The constraint is: must be above parents
        # So min_possible is correct
        
        row[commit] = min_possible
        max_row = max(max_row, min_possible)
    
    return row
```

Hmm, but this might create gaps. Let me think with an example:

```
Commits (oldest to newest): E, D, C, B, A
Parents: A→C, B→D, C→E, D→E

Processing order: E, D, C, B, A
- E: no parents, row = 0
- D: parent E at row 0, so row = 1
- C: parent E at row 0, so row = 1  (same as D!)
- B: parent D at row 1, so row = 2
- A: parent C at row 1, so row = 2  (same as B!)

Result:
Row 2: A  B
Row 1: C  D
Row 0:   E
```

This naturally compacts! The key insight is that processing in time order (oldest first) and always taking the minimum valid row achieves the compaction automatically.

### Phase 2: Column Assignment

Now we need to assign x-positions. This is about:
1. Keeping the same "branch" in the same column where possible
2. Minimizing edge crossings
3. Handling merges and forks

**Simple approach**: Track "active lanes"

```python
def assign_columns(commits, rows):
    # Group commits by row
    by_row = defaultdict(list)
    for c in commits:
        by_row[rows[c]].append(c)
    
    column = {}
    next_free_column = 0
    commit_to_lane = {}  # Track which lane a commit's line of development is in
    
    # Process from bottom (oldest) to top (newest)
    for row in sorted(by_row.keys()):
        commits_in_row = by_row[row]
        
        for commit in commits_in_row:
            # If this commit has a parent whose lane is free, use it
            # Otherwise, allocate a new lane
            
            parent_lanes = [commit_to_lane[p] for p in commit.parents if p in commit_to_lane]
            
            if parent_lanes:
                # Prefer to continue in parent's lane
                # If multiple parents (merge), pick one and the others will have edges
                chosen_lane = parent_lanes[0]  # Could be smarter here
            else:
                chosen_lane = next_free_column
                next_free_column += 1
            
            column[commit] = chosen_lane
            commit_to_lane[commit] = chosen_lane
    
    return column
```

This is a simplification - real implementations get more sophisticated about lane reuse and minimizing crossings.

## Final Algorithm Summary

1. **Collect all commits** from the repository
2. **Topological sort by time** (oldest first)
3. **Assign rows** (y-positions) greedily - each commit gets `max(parent_rows) + 1`
4. **Assign columns** (x-positions) by tracking active lanes

## Edge Cases

### Multiple Roots
Some repos have multiple root commits (orphan branches, or multiple initial commits). These are handled naturally - each root gets row 0, and they'll be in different columns.

### Octopus Merges
A commit with multiple parents - the row must be above ALL parents, so it's `max(all_parent_rows) + 1`.

### Very Wide Graphs
If there are many parallel branches, we get many columns. May need horizontal scrolling or collapsing inactive branches.

## Implementation Notes

For the actual UI:
- Each row has a fixed height
- Each column has a fixed width
- Draw commits as dots/circles
- Draw edges (parent relationships) as lines, potentially with curves for crossings
- Color-code by branch if possible

## Stress Test: Does Time Consistency Hold?

Let me verify the algorithm actually satisfies Constraint 2 (time consistency).

**Constraint 2**: For commits a, b with no ancestor relationship:
```
if time(a) > time(b) then row(a) ≤ row(b)
```

With our algorithm (process oldest first, assign `max(parent_rows) + 1`):

Consider commits A (newer) and B (older) with no ancestor relationship.

- B is processed first (older)
- A is processed later (newer)
- B gets some row `r_b = max(B's parent rows) + 1`
- A gets some row `r_a = max(A's parent rows) + 1`

Is `r_a ≤ r_b` guaranteed? **NO!**

Counter-example:
```
Time    Commit    Parents
----    ------    -------
T4      A         C
T3      B         (root)
T2      C         (root)
```

Processing order: C, B, A
- C: row = 0 (root)
- B: row = 0 (root)
- A: parent C at row 0, so row = 1

Result:
```
Row 1: A
Row 0: C  B
```

A is at row 1, B is at row 0. A is newer than B, but row(A) > row(B). This violates time consistency!

**But wait** - is this actually a problem? Let me re-examine what we want.

## Re-examining the Requirements

The original requirement was:
> "Two commits in branch A and B can be side by side, but if the next two commits in A are both older than the next commit in B, then B has to skip at least one row."

Let me parse this more carefully. I think the intuition is:

**Reading the graph top-to-bottom should feel roughly like reading time backwards** (newest to oldest). But we don't want to be *strict* about it because that wastes space.

The question is: when MUST we enforce time ordering, and when can we relax it?

### Proposal: Time Ordering Within Ancestor Chains Only

Maybe the rule should be:
- Within a single line of development (ancestor chain), time must be respected
- Between unrelated commits, we allow flexibility

But ancestor chains already respect time by the topological constraint! Parents are always older than children, and they're always in higher-numbered rows.

So actually, **the current algorithm is fine**. The "time consistency" constraint I wrote earlier is too strict.

### What About Parallel Branches?

The concern might be visual: if branch A and branch B are side by side, and you're scanning down, you expect to be going back in time. But if commits jump around in time between columns, it's confusing.

Let me think about this differently. Consider:

```
     col0  col1
row0:  A    B
row1:  C    D  
row2:     E
```

Where times are: A=T5, B=T4, C=T3, D=T2, E=T1

Reading column 0: A(T5) → C(T3) - going back in time ✓
Reading column 1: B(T4) → D(T2) → E(T1) - going back in time ✓
Reading row 0: A(T5), B(T4) - both recent
Reading row 1: C(T3), D(T2) - both older

This feels right! The rows are roughly "eras" even if not strictly time-ordered.

### The Actual Invariant

I think what we want is:

**Invariant**: Within a row, commits should be "roughly contemporary". More precisely, no commit in row N should be an ancestor of a commit in row M where M > N.

This is exactly the topological constraint, which our algorithm satisfies!

## Refined Understanding

The algorithm is:
1. Process commits oldest-first
2. Each commit goes in row = `max(parent_rows) + 1` (or 0 if root)
3. This automatically achieves compaction while maintaining topological order

The "time" aspect is a soft property:
- Commits in higher rows (lower numbers) tend to be newer
- But there's no strict guarantee between unrelated commits
- That's okay! The visual makes sense because each column reads like a timeline

## What About the Original Example?

> "if the next two commits in A are both older than the next commit in B, then B has to skip at least one row"

```
Branch A: A1 (T5), A2 (T3), A3 (T1)  [A1 → A2 → A3]
Branch B: B1 (T4), B2 (T2)           [B1 → B2]
Common ancestor: ROOT

Let's say A3 and B2 both have ROOT as parent.
```

Processing order by time: ROOT, A3, B2, A2, B1, A1

- ROOT: row 0
- A3 (T1): parent ROOT, row = 1
- B2 (T2): parent ROOT, row = 1  
- A2 (T3): parent A3, row = 2
- B1 (T4): parent B2, row = 2
- A1 (T5): parent A2, row = 3

Result:
```
row3: A1
row2: A2  B1
row1: A3  B2
row0:  ROOT
```

Hmm, but A2 (T3) and B1 (T4) are in the same row, even though A2 is older. Is this bad?

I don't think so - they're at the same "depth" from their common ancestor. The visual still makes sense: both are "one step up" from their respective parents.

But wait, the original requirement said "B has to skip a row" in some case. Let me re-read...

> "if the next two commits in A are both older than the next commit in B, then B has to skip at least one row"

I think this is saying: if you have:
- Branch A at row N with commits A1, A2 where A2 is the parent
- Branch B at row N with commit B1
- And A1 and A2 are BOTH older than B1

Then B1 should be at a higher row (lower number) than A2.

Let me construct this:
```
Branch A: A1 (T2) → A2 (T1)
Branch B: B1 (T4)
All rooted at ROOT (T0)
```

Wait, that can't be right. If A1 → A2, then A1 is the child and A2 is the parent. So A1 is newer than A2 (typically). Let me use clearer names:

```
Branch A: A_head (T2) → A_middle (T1) → ROOT (T0)
Branch B: B_head (T4) → ROOT (T0)
```

Processing: ROOT, A_middle, A_head, B_head

- ROOT: row 0
- A_middle (T1): parent ROOT, row = 1
- A_head (T2): parent A_middle, row = 2
- B_head (T4): parent ROOT, row = 1

Result:
```
row2: A_head
row1: A_middle  B_head
row0:    ROOT
```

Here B_head (T4) is at row 1, same as A_middle (T1), even though B_head is much newer.

Is this what the requirement was concerned about? Should B_head be at row 2 instead?

The argument for row 2: "B_head is newer than A_head, so it should be at least as high"
The argument for row 1: "B_head is only one commit from ROOT, so depth-wise it's at level 1"

I think **the depth-based approach is correct**. The alternative (strict time ordering) would create gaps:

```
row3: 
row2: A_head  B_head   <- B_head here due to time, but wastes row3
row1: A_middle
row0:    ROOT
```

This wastes vertical space without adding clarity.

## Conclusion

The algorithm I proposed is correct:
1. Process oldest-first
2. Row = max(parent rows) + 1
3. This gives topological order with good compaction
4. Time is a soft property (tends to increase going up, but not strictly)

The original intuition about "skipping rows" was probably about a different case, or was an over-specification. The depth-based approach produces sensible, compact graphs.

## Alternative Formalization: Compression from Strict Time Ordering

Let's start from the other direction: begin with strict time ordering, then compress.

### Starting Point: Strict Time Order

Every commit gets its own row, ordered by time. If we have N commits, we have N rows.

```
row0: A (T5)
row1: B (T4)
row2: C (T3)
row3: D (T2)
row4: E (T1)
```

This is "true" (respects time perfectly) but wastes space.

### The Compression Question

When can we merge two rows into one?

**Necessary condition**: Two commits can share a row only if they're in different columns (different branches/lanes).

**But what else?** This is the key question.

### Your Intuition: "No commit outside the row is between two commits inside the row"

Let me formalize this. If commits X and Y are in the same row, then for any commit Z NOT in that row:
- It's not the case that `time(X) > time(Z) > time(Y)` (Z is not temporally between them)

Equivalently: **commits in a row must be temporally contiguous** - there's no "gap" in time where other commits live.

Let me test this with an example:

```
Commits by time: A(T5), B(T4), C(T3), D(T2), E(T1)
Branches: A→C→E (branch 1), B→D→E (branch 2)
```

Can A and B share a row? 
- Is there any commit Z where T5 > time(Z) > T4? No.
- ✓ They can share a row.

Can A and C share a row?
- Is there any commit Z where T5 > time(Z) > T3? Yes, B at T4.
- ✗ They cannot share a row.

Can B and C share a row?
- Is there any commit Z where T4 > time(Z) > T3? No.
- ✓ They can share a row.

So valid rows under this rule:
- {A, B} ✓
- {B, C} ✓
- {C, D} ✓
- {D, E} - wait, E is T1, D is T2, nothing between, ✓
- {A, B, C}? Is there Z where T5 > time(Z) > T3 and Z ∉ {A,B,C}? 
  - Only candidates are D(T2) and E(T1), both < T3. 
  - ✓ Valid!

Hmm, so {A, B, C} could all be in one row? That seems too aggressive - A is the child of C, they shouldn't be in the same row!

### Missing Constraint: Topological

Right, we still need the topological constraint. Commits in the same row cannot have an ancestor relationship.

So the full rule is:
1. **Topological**: No two commits in the same row can have an ancestor-descendant relationship
2. **Temporal contiguity**: No commit outside the row falls temporally between any two commits in the row

### Let's Re-examine

```
Commits: A(T5), B(T4), C(T3), D(T2), E(T1)
Ancestry: A→C→E, B→D→E
```

Can A and B share a row?
- Topological: A and B are not ancestors of each other ✓
- Temporal: Nothing between T5 and T4 ✓
- **Result: Yes**

Can C and D share a row?
- Topological: C and D are not ancestors of each other ✓
- Temporal: Nothing between T3 and T2 ✓
- **Result: Yes**

Can A and D share a row?
- Topological: A and D are not ancestors of each other ✓
- Temporal: B(T4) and C(T3) are between T5 and T2 ✗
- **Result: No**

That last one is interesting! A(T5) and D(T2) can't share a row even though they're unrelated, because B and C would be "skipped over."

### What Does This Give Us?

With these two constraints, I think the algorithm is:

1. Sort commits by time
2. Greedily group consecutive (in time) commits into rows, subject to:
   - No two commits in a row can be ancestors
   - (Temporal contiguity is automatic if we group consecutively)

Let me try:

```
Sorted: A(T5), B(T4), C(T3), D(T2), E(T1)

Row 0: Start with A
  - Can we add B? A and B not ancestors. ✓ Add B.
  - Can we add C? C is ancestor of A. ✗ Stop.
Row 0 = {A, B}

Row 1: Start with C
  - Can we add D? C and D not ancestors. ✓ Add D.
  - Can we add E? E is ancestor of both C and D. ✗ Stop.
Row 1 = {C, D}

Row 2: Start with E
  - Nothing left.
Row 2 = {E}

Result:
row0: A  B
row1: C  D
row2:   E
```

This matches my earlier algorithm! But now I understand *why* it works.

### The Connection

The "process oldest first, row = max(parent_rows) + 1" algorithm produces the same result because:
- It naturally groups commits at the same "depth" together
- Commits at the same depth from a common ancestor tend to be temporally contiguous
- The topological constraint is explicitly enforced

But the "temporal contiguity" view gives us a different intuition: **we're compressing a strict time ordering by merging adjacent time-slices, but only when doing so doesn't skip over any commits.**

### A Case Where They Might Differ?

Let me try to construct a case where the two approaches give different answers...

```
Commits: A(T6), B(T5), C(T4), D(T3), E(T2), F(T1)
Ancestry: A→C→E→F, B→D→F (two branches merging at F)
```

**Greedy consecutive grouping:**
- Row 0: A, then B? A and B not ancestors. ✓ {A, B}
- Row 1: C, then D? C and D not ancestors. ✓ {C, D}
- Row 2: E, then F? F is ancestor of E. ✗ {E}
- Row 3: {F}

```
row0: A  B
row1: C  D
row2: E
row3:   F
```

**Depth-based (process oldest first):**
- F: row 0
- E: parent F at 0, row 1
- D: parent F at 0, row 1
- C: parent E at 1, row 2
- B: parent D at 1, row 2
- A: parent C at 2, row 3

```
row3: A
row2: C  B
row1: E  D
row0:   F
```

Wait, these are different! The depth-based one puts B at row 2, but the greedy consecutive one puts B at row 0.

Let me check if the greedy result violates anything:
- Row 0 has A(T6) and B(T5) - temporally adjacent ✓, not ancestors ✓
- Row 1 has C(T4) and D(T3) - temporally adjacent ✓, not ancestors ✓

But visually:
```
row0: A  B
row1: C  D
row2: E
row3:   F
```

The edge from B to D skips row 1's C! And the edge from A to C is fine. But B→D goes from row 0 to row 1, that's fine actually.

Hmm wait, let me re-examine. In the greedy approach:
- A is at row 0, its parent C is at row 1. Good.
- B is at row 0, its parent D is at row 1. Good.
- C is at row 1, its parent E is at row 2. Good.
- D is at row 1, its parent F is at row 3. **Skips row 2!**

Is that a problem? The edge D→F would have to pass through row 2, where E lives. 

In the depth-based approach:
- Every parent is exactly one row below. No skipping.

### The Missing Constraint?

I think the temporal contiguity rule isn't quite right, or isn't sufficient. What we really want is:

**Every edge goes to an adjacent row** (no skipping rows)

This is equivalent to: **row = max(parent_rows) + 1**, the depth-based rule.

The temporal contiguity idea captures something, but it's not the right formalization. The right formalization is depth-based.

### So What Was Your Intuition?

I think your intuition about "skipping rows" was actually about **edges**, not about commits in rows:

> "if the next two commits in A are both older than the next commit in B, then B has to skip at least one row"

Rephrased: If B's parent is much older than B (with other commits in between temporally), B can't be placed high up - it has to be placed just one row above its parent, even if that means B ends up "lower" (higher row number) than temporally-older commits from other branches.

This is exactly what the depth-based algorithm does!

### Revised Conclusion

The correct formalization is **depth-based**:
- Row = max(parent_rows) + 1
- This ensures no edges skip rows
- Commits at the same depth can share a row
- Time is used for processing order (affects column assignment and tie-breaking)

The "temporal contiguity" idea was a good intuition but led to a different (and I think worse) algorithm that allows edges to skip rows.

## More Examples of Temporal Contiguity Approach

Let me work through more examples to understand this better.

### Algorithm Recap

1. Sort commits by time (newest first)
2. Greedily assign to rows, grouping consecutive commits if they're not ancestors
3. Constraints:
   - No two commits in same row can be ancestors
   - Commits in a row must be temporally contiguous (no commits outside the row fall between them in time)

### Example 1: Simple Fork

```
Commits: A(T4), B(T3), C(T2), D(T1)
Ancestry: A→C→D, B→D (fork at D, no merge)
```

Processing (newest first): A, B, C, D

- Row 0: A. Can we add B? A and B not ancestors ✓. {A, B}
- Can we add C? C is ancestor of A ✗. Stop row 0.
- Row 1: C. Can we add D? D is ancestor of C ✗. Stop row 1.
- Row 2: D.

```
row0: A  B
row1: C
row2:   D
```

Edges: A→C (0→1), B→D (0→2), C→D (1→2)

Note: B→D skips row 1! Is this okay?

Visually, B is at T3, C is at T2. B is newer than C but they're on different branches. The edge B→D passes by C's row but doesn't intersect C (different column).

### Example 2: Long Branch vs Short Branch

```
Commits: A(T5), B(T4), C(T3), D(T2), E(T1)
Ancestry: A→B→C→D→E (one long branch)
         No other branches - this is linear!
```

Processing: A, B, C, D, E

- Row 0: A. Can we add B? B is ancestor of A ✗. Stop.
- Row 1: B. Can we add C? C is ancestor of B ✗. Stop.
- Row 2: C. Can we add D? D is ancestor of C ✗. Stop.
- Row 3: D. Can we add E? E is ancestor of D ✗. Stop.
- Row 4: E.

```
row0: A
row1: B
row2: C
row3: D
row4: E
```

Linear history = no compression possible. Makes sense!

### Example 3: Two Independent Branches

```
Commits: A(T6), B(T5), C(T4), D(T3), E(T2), F(T1)
Ancestry: A→C→E (branch 1), B→D→F (branch 2)
         No common ancestor! (orphan branches)
```

Processing: A, B, C, D, E, F

- Row 0: A. Add B? Not ancestors ✓. {A, B}
- Add C? C is ancestor of A ✗. Stop.
- Row 1: C. Add D? Not ancestors ✓. {C, D}
- Add E? E is ancestor of C ✗. Stop.
- Row 2: E. Add F? Not ancestors ✓. {E, F}

```
row0: A  B
row1: C  D
row2: E  F
```

Perfect compression! Two parallel timelines, 3 rows instead of 6.

Edges: A→C (0→1), B→D (0→1), C→E (1→2), D→F (1→2)

All edges span exactly 1 row. 

### Example 4: The Problematic Case (Uneven Branches)

```
Commits: A(T6), B(T5), C(T4), D(T3), E(T2), F(T1)
Ancestry: A→C→E→F, B→D→F
         (branch 1 has 3 commits before merge, branch 2 has 2)
```

Processing: A, B, C, D, E, F

- Row 0: A. Add B? Not ancestors ✓. {A, B}
- Add C? C is ancestor of A ✗. Stop.
- Row 1: C. Add D? Not ancestors ✓. {C, D}
- Add E? E is ancestor of C ✗. Stop.
- Row 2: E. Add F? F is ancestor of E ✗. Stop.
- Row 3: F.

```
row0: A  B
row1: C  D
row2: E
row3:   F
```

Edges: A→C (0→1), B→D (0→1), C→E (1→2), D→F (1→3), E→F (2→3)

The edge D→F skips row 2. D is at row 1, F is at row 3.

**Is this actually bad?** Let me think about what this looks like:

```
     col0  col1
row0:  A    B
       |    |
row1:  C    D
       |     \
row2:  E      |
       |      |
row3:  F <----+
```

The line from D to F runs alongside column 0's E→F edge. It's a bit odd but... maybe it's fine? It accurately represents that D was committed at T3 and F at T1, with E(T2) in between.

### Example 5: What Depth-Based Would Give for Example 4

```
Commits: A(T6), B(T5), C(T4), D(T3), E(T2), F(T1)
Ancestry: A→C→E→F, B→D→F
```

Depth-based (oldest first): F, E, D, C, B, A

- F: row 0
- E: parent F(0), row 1
- D: parent F(0), row 1
- C: parent E(1), row 2
- B: parent D(1), row 2
- A: parent C(2), row 3

```
row3: A
row2: C  B
row1: E  D
row0:   F
```

Edges: All exactly 1 row apart.

**But look at the times!**
- Row 3: A(T6)
- Row 2: C(T4), B(T5)
- Row 1: E(T2), D(T3)
- Row 0: F(T1)

B(T5) is below A(T6) even though B is older. That's expected.
But B(T5) is at the same row as C(T4). B is newer!

And D(T3) is at the same row as E(T2). D is newer!

So in the depth-based version, reading across a row doesn't give you contemporaneous commits.

### Example 6: Why Temporal Might Be Better

Consider you're looking at the graph and asking "what was happening around T4?"

**Temporal approach (row 1 = {C, D} where C=T4, D=T3):**
Looking at row 1, you see C and D, times T4 and T3. Close together!

**Depth approach (row 2 = {C, B} where C=T4, B=T5):**
Looking at row 2, you see C and B, times T4 and T5. Also close, but B is newer.

Hmm, both seem reasonable actually.

### Example 7: More Extreme Uneven Branches

```
Commits: A(T10), B(T9), C(T8), D(T7), E(T6), F(T5), G(T4), H(T3), I(T2), J(T1)
Ancestry: A→B→C→D→E→F→G→H→I→J (10-commit branch 1)
         Plus: X(T5.5) → J (1-commit branch 2, between F and G in time)
         
Actually let me simplify:
Commits: A(T5), B(T4), C(T3), D(T2), E(T1), X(T4.5)
Ancestry: A→B→C→D→E (main branch), X→E (short branch)
```

Wait, T4.5 means X is between A(T5) and B(T4). Let me use integers:

```
Commits: A(T6), X(T5), B(T4), C(T3), D(T2), E(T1)
Ancestry: A→B→C→D→E, X→E
```

**Temporal approach:**
Processing: A, X, B, C, D, E

- Row 0: A. Add X? Not ancestors ✓. {A, X}
- Add B? B is ancestor of A ✗. Stop.
- Row 1: B. Add C? C is ancestor of B ✗. Stop.
- Row 2: C. Add D? D is ancestor of C ✗. Stop.
- Row 3: D. Add E? E is ancestor of D ✗. Stop.
- Row 4: E.

```
row0: A  X
row1: B
row2: C
row3: D
row4:   E
```

Edge X→E goes from row 0 to row 4! That's 4 rows of skipping.

**Depth approach:**
Processing: E, D, C, B, X, A

- E: row 0
- D: parent E(0), row 1
- C: parent D(1), row 2
- B: parent C(2), row 3
- X: parent E(0), row 1
- A: parent B(3), row 4

```
row4: A
row3: B
row2: C
row1: D  X
row0:   E
```

Edge X→E is just 1 row.

### The Tradeoff is Clear Now

**Temporal contiguity:**
- Rows represent time slices
- Reading a row = "what was happening around this time"
- But edges can skip many rows if branches have different lengths

**Depth-based:**
- Rows represent topological depth
- All edges span exactly 1 row (clean visual)
- But commits in a row may be from very different times

### Which is Better?

I dismissed temporal too quickly. Let me reconsider.

The skipping edges in temporal are actually *informative*. When X→E skips 4 rows, it's telling you "X was committed way after E, even though X's parent is E." That's useful information!

In the depth-based version, X and D are on the same row, even though X(T5) is much newer than D(T2). That hides the temporal relationship.

**Maybe temporal IS what you want?**

The question is: what's the primary purpose of the graph?

1. **Understanding branch structure**: Depth-based is cleaner
2. **Understanding when things happened**: Temporal is more accurate

For a git history viewer, I could see arguments both ways...

## More Temporal Examples

### Example 8: The "Stale Branch" Case

This is a really common real-world scenario: someone creates a feature branch, works on it, then it sits for a while, then main advances a lot, then they come back.

```
Timeline:
T1: ROOT created
T2: feature branch created from ROOT (commit F1)
T3: main advances (commit M1)
T4: main advances (commit M2)
T5: main advances (commit M3)
T6: feature branch resumes (commit F2, parent F1)

Commits: F2(T6), M3(T5), M2(T4), M1(T3), F1(T2), ROOT(T1)
Ancestry: F2→F1→ROOT, M3→M2→M1→ROOT
```

**Temporal approach:**
Processing: F2, M3, M2, M1, F1, ROOT

- Row 0: F2. Add M3? Not ancestors ✓. {F2, M3}
- Add M2? M2 is ancestor of M3 ✗. Stop.
- Row 1: M2. Add M1? M1 is ancestor of M2 ✗. Stop.
- Row 2: M1. Add F1? Not ancestors ✓. {M1, F1}
- Add ROOT? ROOT is ancestor of both ✗. Stop.
- Row 3: ROOT.

```
row0: F2  M3
row1:     M2
row2: F1  M1
row3:   ROOT
```

Edges:
- F2→F1: 0→2 (skips row 1!)
- M3→M2: 0→1 ✓
- M2→M1: 1→2 ✓
- M1→ROOT: 2→3 ✓
- F1→ROOT: 2→3 ✓

The F2→F1 edge skipping row 1 visually shows "this branch was stale - main advanced while feature sat idle."

**Depth approach:**
- ROOT: row 0
- F1: row 1
- M1: row 1
- M2: row 2
- M3: row 3
- F2: row 2

```
row3:     M3
row2: F2  M2
row1: F1  M1
row0:   ROOT
```

Here F2(T6) is at the same row as M2(T4). But F2 was committed 2 time units after M2! The depth view hides that the feature branch was stale.

### Example 9: Interleaved Work

Two developers working in parallel, commits interleaved in time:

```
T1: ROOT
T2: A1 (Alice, parent ROOT)
T3: B1 (Bob, parent ROOT)
T4: A2 (Alice, parent A1)
T5: B2 (Bob, parent B1)
T6: A3 (Alice, parent A2)

Commits: A3(T6), B2(T5), A2(T4), B1(T3), A1(T2), ROOT(T1)
Ancestry: A3→A2→A1→ROOT, B2→B1→ROOT
```

**Temporal approach:**
Processing: A3, B2, A2, B1, A1, ROOT

- Row 0: A3. Add B2? Not ancestors ✓. {A3, B2}
- Add A2? A2 is ancestor of A3 ✗. Stop.
- Row 1: A2. Add B1? Not ancestors ✓. {A2, B1}
- Add A1? A1 is ancestor of A2 ✗. Stop.
- Row 2: A1. Add ROOT? ROOT is ancestor ✗. Stop.
- Row 3: ROOT.

```
row0: A3  B2
row1: A2  B1
row2: A1
row3:   ROOT
```

Edges:
- A3→A2: 0→1 ✓
- B2→B1: 0→1 ✓
- A2→A1: 1→2 ✓
- B1→ROOT: 1→3 (skips row 2!)
- A1→ROOT: 2→3 ✓

Hmm, B1→ROOT skips a row. That's because A1 was committed between B1 and ROOT.

**Depth approach:**
- ROOT: row 0
- A1: row 1
- B1: row 1
- A2: row 2
- B2: row 2
- A3: row 3

```
row3: A3
row2: A2  B2
row1: A1  B1
row0:   ROOT
```

All edges exactly 1 row. But A2(T4) and B2(T5) are in the same row even though they're different times.

### Example 10: What Does "Skipping" Really Mean Visually?

Let me think about what it looks like when an edge skips rows.

```
Temporal Example 9:
     col0  col1
row0: A3    B2
      |     |
row1: A2    B1
      |      \
row2: A1      \
      |        \
row3: ROOT <----+
```

The B1→ROOT edge runs down from row 1 to row 3, passing by row 2. It doesn't intersect A1 (different column), but it does span multiple rows.

Is this bad? I think it's actually **informative**:
- It shows that between B1 and ROOT, something else happened (A1)
- The visual "length" of the edge corresponds to temporal distance

Compare to depth-based:
```
Depth Example 9:
     col0  col1
row3: A3
      |
row2: A2    B2
      |     |
row1: A1    B1
      |    /
row0: ROOT<+
```

Here B1→ROOT is only 1 row, same as A1→ROOT. You lose the information that A1 was committed between B1 and ROOT.

### Example 11: Extreme Case - Very Old Branch Revived

```
T1: ROOT
T2-T100: 99 commits on main (M1...M99)
T101: Feature commit F1 with parent ROOT

Ancestry: M99→M98→...→M1→ROOT, F1→ROOT
```

**Temporal:**
F1 would be at row 0 (newest), and M99 could join it.
Then M98 at row 1, etc.
The edge F1→ROOT would span 100 rows!

Visually, you'd see a very long line from F1 down to ROOT, showing "this commit's parent is ancient."

**Depth:**
F1 would be at row 1 (depth 1 from ROOT), same row as M1.
F1(T101) and M1(T2) at the same row! That's a 99-time-unit difference hidden.

### The Case FOR Temporal

1. **Rows = time slices**: Looking at a row tells you "what was happening around this time"
2. **Long edges are meaningful**: They show "this commit was based on something old"
3. **Stale branches are visible**: You can see when work was abandoned and resumed
4. **Interleaved work is visible**: You see the actual chronology

### The Case FOR Depth

1. **Clean edges**: Every edge spans exactly 1 row, easier to follow
2. **Structural clarity**: Shows the branch/merge structure clearly
3. **Compact for unbalanced histories**: A 1-commit branch doesn't create 99 rows of empty space

### A Hybrid?

What if we:
1. Use temporal for row assignment
2. But allow a maximum edge length (e.g., 5 rows)
3. If an edge would be longer, insert "..." or collapse the empty space

This would give temporal benefits while avoiding extreme visual stretching.

### Actually, Let's Reconsider the Temporal Algorithm

The greedy algorithm I described might not be optimal. Let me think about what we're actually optimizing.

**Goal**: Minimize rows while respecting:
1. Topological constraint (no ancestors in same row)
2. Temporal contiguity (no gaps)

The greedy approach (scan newest-first, add to current row if possible) might not find the optimal solution.

But actually... I think it does? Because:
- We process in time order
- We greedily pack
- Temporal contiguity is automatically satisfied (we never skip a commit)
- Topological constraint is checked

Let me verify with a tricky case...

```
Commits: A(T5), B(T4), C(T3), D(T2), E(T1)
What if: A→E, B→D→E, C→E (three branches from E)
```

Processing: A, B, C, D, E

- Row 0: A. Add B? Not ancestors ✓. Add C? Not ancestors ✓. {A, B, C}
- Add D? D is ancestor of B ✗. Stop.
- Row 1: D. Add E? E is ancestor of D ✗. Stop.
- Row 2: E.

```
row0: A  B  C
row1:    D
row2:      E
```

Edges: A→E (0→2), B→D (0→1), C→E (0→2), D→E (1→2)

Two edges skip row 1: A→E and C→E.

Is this the minimum rows? Yes - we can't do better than 3 rows because E must be alone (ancestor of all), and D must be separate from B.

Could we arrange it differently? What if we didn't put A, B, C together?

Row 0: A, B (not C)
Row 1: C, D (C not ancestor of D, D not ancestor of C)
Row 2: E

```
row0: A  B
row1: C  D
row2:      E
```

Edges: A→E (0→2), B→D (0→1), C→E (1→2), D→E (1→2)

Only one edge skips (A→E). This seems better!

So the greedy algorithm isn't optimal! We got {A,B,C} in row 0, but {A,B} in row 0 and {C,D} in row 1 is better (fewer skipping edges).

### Revised Goal

Maybe we want to:
1. **Primary**: Minimize total rows
2. **Secondary**: Minimize total edge-skip distance (sum of |row(child) - row(parent) - 1|)

This is more complex than a simple greedy algorithm...

### Is This a Known Problem?

This feels like it might be related to graph drawing algorithms, specifically layered graph drawing (Sugiyama-style). The standard approach there is:

1. Assign layers (rows) to minimize edge length
2. Order nodes within layers to minimize crossings
3. Assign x-coordinates

The layer assignment problem with the goal of minimizing edge lengths is actually NP-hard in general, but there are good heuristics.

For our specific constraints (temporal contiguity + topological), I wonder if there's a polynomial solution...

## Focusing on Temporal: More Examples

### Example 12: Merge Commit

```
T1: ROOT
T2: A (parent ROOT) - branch A starts  
T3: B (parent ROOT) - branch B starts
T4: C (parent A) - branch A continues
T5: D (parent B) - branch B continues
T6: M (parents C, D) - merge!

Commits: M(T6), D(T5), C(T4), B(T3), A(T2), ROOT(T1)
Ancestry: M→C→A→ROOT, M→D→B→ROOT
```

**Temporal approach:**
Processing: M, D, C, B, A, ROOT

- Row 0: M. Add D? D is parent of M ✗. Stop.
- Row 1: D. Add C? Not ancestors ✓. {D, C}
- Add B? B is ancestor of D ✗. Stop.
- Row 2: B. Add A? Not ancestors ✓. {B, A}
- Add ROOT? ROOT is ancestor of both ✗. Stop.
- Row 3: ROOT.

```
row0:    M
row1: C    D
row2: A    B
row3:   ROOT
```

Edges: M→C (0→1), M→D (0→1), C→A (1→2), D→B (1→2), A→ROOT (2→3), B→ROOT (2→3)

All edges span exactly 1 row! The temporal approach naturally handles this merge beautifully.

### Example 13: Merge with Uneven Branches

```
T1: ROOT
T2: A (parent ROOT)
T3: B (parent A)
T4: C (parent ROOT) - short branch
T5: M (parents B, C) - merge

Commits: M(T5), C(T4), B(T3), A(T2), ROOT(T1)
Ancestry: M→B→A→ROOT, M→C→ROOT
```

**Temporal:**
Processing: M, C, B, A, ROOT

- Row 0: M. Add C? C is parent of M ✗. Stop.
- Row 1: C. Add B? Not ancestors ✓. {C, B}
- Add A? A is ancestor of B ✗. Stop.
- Row 2: A. Add ROOT? ROOT is ancestor ✗. Stop.
- Row 3: ROOT.

```
row0:      M
row1:   B  C
row2:   A
row3: ROOT
```

Edges: M→B (0→1), M→C (0→1), B→A (1→2), C→ROOT (1→3), A→ROOT (2→3)

C→ROOT skips row 2. This shows that C branched from ROOT but A was committed in between.

### Example 14: Fast-Forward Scenario

```
T1: ROOT
T2: A (parent ROOT) - main
T3: B (parent A) - main
T4: C (parent A) - feature branch from A
T5: D (parent B) - main continues
T6: E (parent C) - feature continues

Commits: E(T6), D(T5), C(T4), B(T3), A(T2), ROOT(T1)
Ancestry: E→C→A→ROOT, D→B→A→ROOT
```

**Temporal:**
Processing: E, D, C, B, A, ROOT

- Row 0: E. Add D? Not ancestors ✓. {E, D}
- Add C? C is ancestor of E ✗. Stop.
- Row 1: C. Add B? Not ancestors ✓. {C, B}
- Add A? A is ancestor of both ✗. Stop.
- Row 2: A. Add ROOT? ROOT is ancestor ✗. Stop.
- Row 3: ROOT.

```
row0: E    D
row1: C    B
row2:    A
row3:  ROOT
```

Edges: E→C (0→1), D→B (0→1), C→A (1→2), B→A (1→2), A→ROOT (2→3)

All edges span 1 row! Beautiful. Shows the parallel development clearly.

### Example 15: The Rebase-Like Pattern

Someone rebases old work onto new main:

```
T1: ROOT
T2: M1 (parent ROOT) - main
T3: M2 (parent M1) - main  
T4: M3 (parent M2) - main
T5: F1 (parent M3) - feature starts from latest main
T6: F2 (parent F1) - feature continues

Commits: F2(T6), F1(T5), M3(T4), M2(T3), M1(T2), ROOT(T1)
Ancestry: F2→F1→M3→M2→M1→ROOT (linear!)
```

**Temporal:**
Linear history = no compression. Each commit gets its own row.

```
row0: F2
row1: F1
row2: M3
row3: M2
row4: M1
row5: ROOT
```

All edges span 1 row. Makes sense - linear is linear.

### Example 16: The "Topic Branch Hell" Pattern

Multiple small topic branches:

```
T1: ROOT
T2: A (parent ROOT) - topic A
T3: B (parent ROOT) - topic B  
T4: C (parent ROOT) - topic C
T5: D (parent ROOT) - topic D

Commits: D(T5), C(T4), B(T3), A(T2), ROOT(T1)
Ancestry: A→ROOT, B→ROOT, C→ROOT, D→ROOT (all independent!)
```

**Temporal:**
Processing: D, C, B, A, ROOT

- Row 0: D. Add C? Not ancestors ✓. Add B? Not ancestors ✓. Add A? Not ancestors ✓. {D, C, B, A}
- Add ROOT? ROOT is ancestor of all ✗. Stop.
- Row 1: ROOT.

```
row0: D  C  B  A
row1:    ROOT
```

All 4 topic branches in one row! Edges: all span 1 row.

This is great - shows "4 parallel things happened, all from ROOT."

### Example 17: Staggered Topic Branches

```
T1: ROOT
T2: A1 (parent ROOT) - topic A starts
T3: B1 (parent ROOT) - topic B starts
T4: A2 (parent A1) - topic A continues
T5: B2 (parent B1) - topic B continues
T6: C1 (parent ROOT) - topic C starts late!

Commits: C1(T6), B2(T5), A2(T4), B1(T3), A1(T2), ROOT(T1)
Ancestry: A2→A1→ROOT, B2→B1→ROOT, C1→ROOT
```

**Temporal:**
Processing: C1, B2, A2, B1, A1, ROOT

- Row 0: C1. Add B2? Not ancestors ✓. {C1, B2}
- Add A2? Not ancestors ✓. {C1, B2, A2}
- Add B1? B1 is ancestor of B2 ✗. Stop.
- Row 1: B1. Add A1? Not ancestors ✓. {B1, A1}
- Add ROOT? ROOT is ancestor of both ✗. Stop.
- Row 2: ROOT.

```
row0: A2  B2  C1
row1: A1  B1
row2:    ROOT
```

Edges:
- A2→A1 (0→1) ✓
- B2→B1 (0→1) ✓
- C1→ROOT (0→2) skips!
- A1→ROOT (1→2) ✓
- B1→ROOT (1→2) ✓

The C1→ROOT skip shows "C1 branched from ROOT, but A1 and B1 were committed in between."

This is informative! It shows C1 is "late to the party" - based on old ROOT while others have made progress.

## The Temporal Algorithm: Formal Statement

```python
def temporal_row_assignment(commits):
    """
    Assign rows to commits based on temporal contiguity.
    
    Constraints:
    1. No two commits in same row can have ancestor relationship
    2. Commits in a row must be temporally contiguous 
       (no commit outside row falls between them in time)
    
    Algorithm:
    - Process commits newest to oldest
    - Greedily add each commit to current row if constraints allow
    - When constraint violated, start new row
    """
    # Sort newest first
    sorted_commits = sorted(commits, key=lambda c: -c.time)
    
    rows = []  # List of sets
    current_row = set()
    
    for commit in sorted_commits:
        # Check if commit can join current row
        can_join = True
        for existing in current_row:
            if is_ancestor(commit, existing) or is_ancestor(existing, commit):
                can_join = False
                break
        
        if can_join and current_row:
            current_row.add(commit)
        else:
            if current_row:
                rows.append(current_row)
            current_row = {commit}
    
    if current_row:
        rows.append(current_row)
    
    # Convert to row assignments
    row_of = {}
    for i, row in enumerate(rows):
        for commit in row:
            row_of[commit] = i
    
    return row_of
```

Wait, I realize this greedy algorithm might group non-contiguous commits. Let me reconsider...

Actually no - because we process in time order, and we only add to the current row, temporal contiguity is guaranteed. If commit X at T5 and commit Y at T3 are in the same row, it means everything between them (T4) was also considered for this row - it either:
1. Was added to this row, or
2. Was rejected due to ancestor constraint, so it started a new row

So the constraint "no commit outside the row falls between them" is automatically satisfied by the greedy left-to-right scan.

## What About Optimality?

The greedy algorithm minimizes rows but may not minimize total edge skip distance. 

From Example with A→E, B→D→E, C→E:
- Greedy gives {A,B,C}, {D}, {E} with skips on A→E and C→E
- Alternative {A,B}, {C,D}, {E} has skip only on A→E

But... is the alternative even valid under temporal contiguity?

A(T5), B(T4), C(T3), D(T2), E(T1)

For {A,B} at row 0 and {C,D} at row 1:
- Row 0: A(T5), B(T4) - contiguous ✓
- Row 1: C(T3), D(T2) - contiguous ✓

Yes, valid! So there are multiple valid row assignments, and greedy doesn't find the one with minimum edge skips.

**But does it matter?** The difference is whether C is in row 0 or row 1. In both cases:
- The A→E edge skips (A is far from E temporally)
- The C→E edge skip depends on where C is

Maybe minimizing skips isn't the right goal. The greedy algorithm has a nice property: **it keeps contemporaneous commits together**.

## Summary: The Temporal Algorithm

1. Sort commits by time (newest first)
2. Greedily assign to rows: add to current row if no ancestor conflict
3. Temporal contiguity is automatic from the greedy scan
4. Edges may skip rows - this is **informative**, showing temporal gaps

The result:
- Rows = time slices
- Long edges = "based on old commit"
- Parallel branches are visually parallel when contemporaneous
- Stale branches are visually obvious

## Next Steps

Implement this in Python and see how it looks on real repositories.
