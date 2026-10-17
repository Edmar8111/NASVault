CREATE TABLE IF NOT EXISTS usuario (
    id INT AUTO_INCREMENT PRIMARY KEY,
    username VARCHAR(100) NOT NULL UNIQUE,
    email VARCHAR(150) NOT NULL UNIQUE,
    data_criacao TIMESTAMP DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS arquivos (
    id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    owner_id INT NOT NULL,
    file_name VARCHAR(255) NOT NULL,
    file_size BIGINT UNSIGNED NOT NULL,
    total_chunks BIGINT UNSIGNED NOT NULL DEFAULT 0,
    file_hash CHAR(64) NOT NULL DEFAULT '',
    status ENUM('uploading', 'ready', 'failed') NOT NULL DEFAULT 'uploading',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_arquivos_file_name (file_name),
    CONSTRAINT fk_arquivos_usuario FOREIGN KEY (owner_id)
        REFERENCES usuario(id) ON UPDATE CASCADE ON DELETE CASCADE
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS arquivo_chunks (
    arquivo_id BIGINT UNSIGNED NOT NULL,
    chunk_id INT UNSIGNED NOT NULL,
    chunk_hash CHAR(64) NOT NULL,
    chunk_content MEDIUMBLOB NOT NULL,
    PRIMARY KEY (arquivo_id, chunk_id),
    CONSTRAINT fk_arquivo_chunks_arquivo FOREIGN KEY (arquivo_id)
        REFERENCES arquivos(id) ON DELETE CASCADE
) ENGINE=InnoDB ROW_FORMAT=DYNAMIC;