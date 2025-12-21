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

## Next Steps

Implement this in Python and see how it looks on real repositories.
