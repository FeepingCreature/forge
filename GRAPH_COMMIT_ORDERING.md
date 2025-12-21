# Git Graph Commit Ordering

## The Problem

We want to display commits in a 2D grid where:
- **Y-axis**: Time (newer commits at top, older at bottom)
- **X-axis**: Different branches/lines of development

We want to compress vertical space (multiple commits can share a row) while preserving temporal relationships.

## The Algorithm

**Input:** A set of commits, each with a timestamp and parent references.

**Output:** A row number for each commit (row 0 at top = newest, higher rows = older).

### Steps

1. **Sort all commits by time, newest first.**

2. **Walk through the sorted list, greedily building rows:**
   - Start with an empty "current row"
   - For each commit (in newest-to-oldest order):
     - Check: is this commit an ancestor of anyone already in the current row, or vice versa?
     - If **no conflict**: add it to the current row
     - If **conflict**: close the current row, start a new row with this commit

3. **Done.** Each row gets consecutive numbers (0, 1, 2, ...).

### Why It Works

Because we process in time order and only add to the "current" row, we get **temporal contiguity** for free: if commits X(T5) and Y(T3) end up in the same row, everything in between (T4) was also considered for that row - it either joined, or it caused a row break.

The only hard constraint is **no ancestors in the same row** - a commit and its parent/grandparent/etc. can't share a row.

## Walkthrough Example

```
Commits (newest first): A(T6), B(T5), C(T4), D(T3), E(T2), F(T1)
Ancestry: A→C→E→F, B→D→F
```

Processing:
- **A(T6)**: Current row empty. Add A. Row 0 = {A}
- **B(T5)**: Is B ancestor of A, or A of B? No. Add B. Row 0 = {A, B}
- **C(T4)**: Is C ancestor of A or B? Yes, C is parent of A. **Conflict!** Close row 0, start row 1 = {C}
- **D(T3)**: Is D ancestor of C? No. Add D. Row 1 = {C, D}
- **E(T2)**: Is E ancestor of C or D? Yes, E is parent of C. **Conflict!** Close row 1, start row 2 = {E}
- **F(T1)**: Is F ancestor of E? Yes. **Conflict!** Close row 2, start row 3 = {F}

Result:
```
row 0: A  B
row 1: C  D
row 2: E
row 3:   F
```

## Key Property: Long Edges Are Informative

Edges can skip rows. This is a feature, not a bug - it visually shows temporal gaps.

Example - a "stale branch" scenario:
```
T1: ROOT
T2: F1 (feature branch starts)
T3: M1 (main advances)
T4: M2 (main advances)
T5: M3 (main advances)
T6: F2 (feature branch resumes, parent F1)
```

Result:
```
row 0: F2  M3
row 1:     M2
row 2: F1  M1
row 3:   ROOT
```

The F2→F1 edge skips row 1, visually showing "this branch was stale - main advanced while feature sat idle."

## Column Assignment (X-axis)

After row assignment, columns are assigned by tracking "lanes":
- Each branch gets a lane
- Continue parent's lane when possible
- Allocate new lanes for new branches
- Draw edges between commits, potentially curving around other lanes
