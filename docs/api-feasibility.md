# Public API feasibility review

Review date: 2026-07-10

Decision: use supervised, headed browser automation for this narrowly defined setting. No
supported public Google Workspace API currently exposes the complete resource model required for
organization-level Gmail blocked-sender rules.

## Official documentation reviewed

- [Gmail API REST reference](https://developers.google.com/workspace/gmail/api/reference/rest)
- [Gmail `users.settings` resource](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.settings)
- [Admin SDK Directory API reference](https://developers.google.com/workspace/admin/directory/reference/rest)
- [Directory API organizational units](https://developers.google.com/workspace/admin/directory/v1/guides/manage-org-units)
- [Block messages from an email address or domain](https://support.google.com/a/answer/2364632)
- [Use address lists to apply settings](https://support.google.com/a/answer/7381367)

## API findings

| Interface | Authentication | Blocked-sender rule | Address-list binding/content | Rejection notice | OU targeting | Decision |
|---|---|---:|---:|---:|---:|---|
| Gmail API `users.settings` | OAuth 2.0; some admin operations require domain-wide delegation | No | No | No | No; user mailbox settings only | Insufficient |
| Admin SDK Directory API | OAuth 2.0 with administrator authorization or domain-wide delegation | No | No | No | Manages OU structure, not Gmail blocked-sender policy | Insufficient |
| Admin console UI | Interactive administrator session and Gmail Settings privilege | Yes | Yes | Yes | Yes | Required supported surface |

The Gmail API documents mailbox settings such as auto-forwarding, IMAP, language, POP, vacation,
delegates, filters, forwarding addresses, and send-as identities. It does not document the Admin
console's organization-level blocked-sender setting. A user filter is not an equivalent substitute:
it applies to one mailbox and cannot represent an organization-level SMTP rejection notice.

The Directory API can read and manage the organization tree, users, groups, roles, and related
directory resources. It does not document Gmail service-policy objects, blocked-sender rules,
address lists used by those rules, or rejection-notice fields.

Google's administrator help documents the required UI workflow: open **Spam, Phishing and
Malware**, configure **Blocked senders**, select or create one or more address lists, optionally
edit the rejection notice, and save. It also says the Gmail Settings administrator privilege is
required and that changes may take up to 24 hours to propagate.

## Authorization boundary

Browser automation is authorized only because the public APIs do not expose the required setting.
It must use the normal headed Admin console and a dedicated persistent Chrome profile. It may not
call captured private endpoints, replay browser requests, copy cookies, hard-code sessions, use
undocumented RPCs, or bypass Google's normal authorization experience.

This review must be repeated before a major release and whenever Google publishes a relevant
Workspace policy API. A supported public API supersedes browser automation when it can express the
same rule/list/notice/OU semantics and preserve the safety controls.
