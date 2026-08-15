"""Linear GraphQL documents, kept separate from provider orchestration."""

TEAMS_QUERY = """
query ($cursor: String) {
  teams (first: 50, after: $cursor) {
    pageInfo { hasNextPage endCursor }
    nodes { id key name description }
  }
}
"""

STATES_QUERY = """
query ($team: String!) {
  team (id: $team) {
    states (first: 50) {
      nodes { id name type position }
    }
  }
}
"""

# A single-issue fetch and a page must select exactly the same shape or
# conflict detection compares differently populated copies of one issue.
ISSUE_FIELDS = """
        id
        identifier
        title
        description
        url
        priorityLabel
        sortOrder
        createdAt
        updatedAt
        startedAt
        completedAt
        dueDate
        state { id name }
        assignee { id displayName }
        creator { id displayName }
        parent { identifier }
        labels (first: 20) { nodes { name } }
"""

ONE_ISSUE_QUERY = f"""
query ($id: String!) {{
  issue (id: $id) {{
{ISSUE_FIELDS}
  }}
}}
"""

ISSUES_QUERY = f"""
query ($team: String!, $cursor: String) {{
  team (id: $team) {{
    issues (first: 100, after: $cursor) {{
      pageInfo {{ hasNextPage endCursor }}
      nodes {{
{ISSUE_FIELDS}
      }}
    }}
  }}
}}
"""

MOVE_MUTATION = """
mutation ($id: String!, $state: String!) {
  issueUpdate (id: $id, input: { stateId: $state }) { success }
}
"""

UPDATE_MUTATION = """
mutation ($id: String!, $input: IssueUpdateInput!) {
  issueUpdate (id: $id, input: $input) { success }
}
"""

CREATE_MUTATION = f"""
mutation ($input: IssueCreateInput!) {{
  issueCreate(input: $input) {{
    success
    issue {{
{ISSUE_FIELDS}
    }}
  }}
}}
"""

USERS_QUERY = """query { users(first: 250) { nodes { id name displayName email } } }"""
LABELS_QUERY = """query { issueLabels(first: 250) { nodes { id name } } }"""

COMMENT_FIELDS = """
        id
        issueId
        body
        url
        createdAt
        updatedAt
        parentId
        user { id name displayName }
        botActor { id name }
        externalUser { id name displayName }
"""

COMMENTS_QUERY = f"""
query ($id: String!, $cursor: String) {{
  issue(id: $id) {{
    comments(first: 50, after: $cursor, orderBy: createdAt) {{
      pageInfo {{ hasNextPage endCursor }}
      nodes {{
{COMMENT_FIELDS}
      }}
    }}
  }}
}}
"""

CREATE_COMMENT_MUTATION = f"""
mutation ($input: CommentCreateInput!) {{
  commentCreate(input: $input) {{
    success
    comment {{
{COMMENT_FIELDS}
    }}
  }}
}}
"""

__all__ = [
    "CREATE_MUTATION",
    "CREATE_COMMENT_MUTATION",
    "COMMENTS_QUERY",
    "ISSUES_QUERY",
    "LABELS_QUERY",
    "MOVE_MUTATION",
    "ONE_ISSUE_QUERY",
    "STATES_QUERY",
    "TEAMS_QUERY",
    "UPDATE_MUTATION",
    "USERS_QUERY",
]
