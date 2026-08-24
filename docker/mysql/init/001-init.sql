CREATE DATABASE IF NOT EXISTS clausemind_demo
  CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE DATABASE IF NOT EXISTS clausemind_knowledge
  CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

GRANT ALL PRIVILEGES ON clausemind_demo.* TO 'demo'@'%';
GRANT ALL PRIVILEGES ON clausemind_knowledge.* TO 'demo'@'%';
FLUSH PRIVILEGES;

USE clausemind_demo;

CREATE TABLE IF NOT EXISTS demo_disassemble_service_middle_info (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  input_json LONGTEXT NOT NULL,
  prompts_json LONGTEXT NOT NULL,
  output_json LONGTEXT NOT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  KEY idx_created_at (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS app_deconstruct_output (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  deconstruct_result LONGTEXT NOT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  KEY idx_created_at (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS demo_one_agent_multiple_tools (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  report_no VARCHAR(128),
  request_json LONGTEXT,
  prompt LONGTEXT,
  agent_name VARCHAR(255),
  response_json LONGTEXT,
  audit_memo LONGTEXT,
  text_messages LONGTEXT,
  tool_messages LONGTEXT,
  time_cost DOUBLE,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  KEY idx_report_no (report_no)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
