"""Monday.com GraphQL documents, kept separate from provider orchestration."""

ME_QUERY = """query { me { id name email } }"""

BOARDS_QUERY = """
query ($page: Int!) {
  boards (limit: 100, page: $page, state: active) {
    id
    name
    description
    url
  }
}
"""

BOARD_SHAPE_QUERY = """
query ($ids: [ID!]) {
  boards (ids: $ids) {
    columns { id title type settings_str }
    groups { id title position }
  }
}
"""

ONE_ITEM_QUERY = """
query ($ids: [ID!]) {
  items (ids: $ids) {
    id
    name
    url
    created_at
    updated_at
    creator { id name }
    group { id title }
    column_values { id type text value }
  }
}
"""

ITEMS_QUERY = """
query ($ids: [ID!], $cursor: String) {
  boards (ids: $ids) {
    items_page (limit: 100, cursor: $cursor) {
      cursor
      items {
        id
        name
        url
        created_at
        updated_at
        creator { id name }
        group { id title }
        column_values { id type text value }
      }
    }
  }
}
"""

MOVE_MUTATION = """
mutation ($item: ID!, $board: ID!, $column: String!, $value: JSON!) {
  change_column_value (item_id: $item, board_id: $board, column_id: $column, value: $value) { id }
}
"""

CHANGE_MULTIPLE_MUTATION = """
mutation ($item: ID!, $board: ID!, $values: JSON!) {
  change_multiple_column_values(item_id: $item, board_id: $board, column_values: $values) { id }
}
"""

CREATE_MUTATION = """
mutation ($board: ID!, $group: String, $name: String!, $values: JSON!) {
  create_item(board_id: $board, group_id: $group, item_name: $name,
              column_values: $values) { id }
}
"""

RENAME_MUTATION = """
mutation ($item: ID!, $board: ID!, $value: String!) {
  change_simple_column_value(
    item_id: $item, board_id: $board, column_id: "name", value: $value
  ) { id }
}
"""

USERS_QUERY = """query { users { id name email } }"""

UPDATE_FIELDS = """
        id
        body
        text_body
        created_at
        updated_at
        edited_at
        creator_id
        creator { id name }
"""

UPDATES_QUERY = f"""
query ($ids: [ID!], $page: Int!) {{
  items(ids: $ids) {{
    id
    updates(limit: 100, page: $page) {{
{UPDATE_FIELDS}
      replies {{
{UPDATE_FIELDS}
      }}
    }}
  }}
}}
"""

CREATE_UPDATE_MUTATION = f"""
mutation ($item: ID!, $body: String!) {{
  create_update(item_id: $item, body: $body) {{
{UPDATE_FIELDS}
  }}
}}
"""

MOVE_GROUP_MUTATION = """
mutation ($item: ID!, $group: String!) {
  move_item_to_group (item_id: $item, group_id: $group) { id }
}
"""

__all__ = [
    "BOARDS_QUERY",
    "BOARD_SHAPE_QUERY",
    "CHANGE_MULTIPLE_MUTATION",
    "CREATE_MUTATION",
    "CREATE_UPDATE_MUTATION",
    "ITEMS_QUERY",
    "ME_QUERY",
    "MOVE_GROUP_MUTATION",
    "MOVE_MUTATION",
    "ONE_ITEM_QUERY",
    "RENAME_MUTATION",
    "USERS_QUERY",
    "UPDATES_QUERY",
]
