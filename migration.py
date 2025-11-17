import psycopg2
import os

conn = psycopg2.connect(os.environ['DATABASE_URL'])
cur = conn.cursor()

cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name='pagamento_parcelado' AND column_name='servico_id'")
existe = cur.fetchone()

if not existe:
    print('Adicionando coluna...')
    cur.execute('ALTER TABLE pagamento_parcelado ADD COLUMN servico_id INTEGER')
    conn.commit()
    
    print('Adicionando FK...')
    cur.execute('ALTER TABLE pagamento_parcelado ADD CONSTRAINT fk_pagamento_parcelado_servico FOREIGN KEY (servico_id) REFERENCES servico(id) ON DELETE SET NULL')
    conn.commit()
    
    print('Criando indice...')
    cur.execute('CREATE INDEX idx_pagamento_parcelado_servico ON pagamento_parcelado(servico_id)')
    conn.commit()
    
    print('PRONTO!')
else:
    print('Ja existe!')

cur.close()
conn.close()