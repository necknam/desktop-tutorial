-- Adds K-water evaluation criteria tables. Existing migrations remain immutable.

CREATE SCHEMA IF NOT EXISTS hr_regulation_pilot;

CREATE TABLE IF NOT EXISTS hr_regulation_pilot.evaluation_result_schema (
    "evaluation_result_schema_id" VARCHAR(64) NOT NULL PRIMARY KEY,
    "institution_id" VARCHAR(64) NOT NULL,
    "schema_name" VARCHAR(255) NOT NULL,
    "version_label" VARCHAR(255) NOT NULL,
    "evaluation_kind" VARCHAR(40) NOT NULL,
    "applicability_condition" JSONB NOT NULL,
    "applicability_note" TEXT,
    "effective_from" DATE,
    "effective_to" DATE,
    "rule_origin" VARCHAR(40) NOT NULL,
    "source_status" VARCHAR(40) NOT NULL,
    "review_status" VARCHAR(40) NOT NULL,
    "calculation_rule" JSONB,
    "calculation_status" VARCHAR(40) NOT NULL,
    "source_note" TEXT
);

CREATE TABLE IF NOT EXISTS hr_regulation_pilot.evaluation_item_definition (
    "evaluation_item_definition_id" VARCHAR(64) NOT NULL PRIMARY KEY,
    "evaluation_result_schema_id" VARCHAR(64) NOT NULL,
    "parent_item_id" VARCHAR(64),
    "skill_id" INT,
    "item_code" VARCHAR(255) NOT NULL,
    "item_name" VARCHAR(255) NOT NULL,
    "value_kind" VARCHAR(40) NOT NULL,
    "aggregation_role" VARCHAR(40) NOT NULL,
    "score_min" NUMERIC(12,3),
    "score_max" NUMERIC(12,3),
    "weight_percent" NUMERIC(6,3),
    "unit" VARCHAR(40),
    "scale_note" TEXT,
    "display_order" INT NOT NULL,
    "definition_note" TEXT,
    CONSTRAINT evaluation_item_definition_schema_code_uq UNIQUE (evaluation_result_schema_id, item_code),
    CONSTRAINT evaluation_item_definition_id_schema_uq UNIQUE (evaluation_item_definition_id, evaluation_result_schema_id)
);

CREATE TABLE IF NOT EXISTS hr_regulation_pilot.evaluation_schema_source (
    "evaluation_schema_source_id" VARCHAR(64) NOT NULL PRIMARY KEY,
    "evaluation_result_schema_id" VARCHAR(64) NOT NULL,
    "evaluation_item_definition_id" VARCHAR(64),
    "regulation_node_id" VARCHAR(64) NOT NULL,
    "source_role" VARCHAR(40) NOT NULL,
    "review_status" VARCHAR(40) NOT NULL,
    "source_note" TEXT,
    CONSTRAINT evaluation_schema_source_id_schema_fk FOREIGN KEY (evaluation_item_definition_id, evaluation_result_schema_id) REFERENCES hr_regulation_pilot.evaluation_item_definition (evaluation_item_definition_id, evaluation_result_schema_id)
);

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'evaluation_result_schema_institution_fk' AND conrelid = 'hr_regulation_pilot.evaluation_result_schema'::regclass) THEN
        ALTER TABLE hr_regulation_pilot.evaluation_result_schema ADD CONSTRAINT evaluation_result_schema_institution_fk FOREIGN KEY (institution_id) REFERENCES hr_regulation_pilot.institution (institution_id);
    END IF;
END $$;

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'evaluation_item_definition_schema_fk' AND conrelid = 'hr_regulation_pilot.evaluation_item_definition'::regclass) THEN
        ALTER TABLE hr_regulation_pilot.evaluation_item_definition ADD CONSTRAINT evaluation_item_definition_schema_fk FOREIGN KEY (evaluation_result_schema_id) REFERENCES hr_regulation_pilot.evaluation_result_schema (evaluation_result_schema_id);
    END IF;
END $$;

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'evaluation_item_definition_parent_fk' AND conrelid = 'hr_regulation_pilot.evaluation_item_definition'::regclass) THEN
        ALTER TABLE hr_regulation_pilot.evaluation_item_definition ADD CONSTRAINT evaluation_item_definition_parent_fk FOREIGN KEY (parent_item_id, evaluation_result_schema_id) REFERENCES hr_regulation_pilot.evaluation_item_definition (evaluation_item_definition_id, evaluation_result_schema_id);
    END IF;
END $$;

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'evaluation_item_definition_skill_fk' AND conrelid = 'hr_regulation_pilot.evaluation_item_definition'::regclass) THEN
        ALTER TABLE hr_regulation_pilot.evaluation_item_definition ADD CONSTRAINT evaluation_item_definition_skill_fk FOREIGN KEY (skill_id) REFERENCES public.skill_tags (skill_id);
    END IF;
END $$;

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'evaluation_schema_source_schema_fk' AND conrelid = 'hr_regulation_pilot.evaluation_schema_source'::regclass) THEN
        ALTER TABLE hr_regulation_pilot.evaluation_schema_source ADD CONSTRAINT evaluation_schema_source_schema_fk FOREIGN KEY (evaluation_result_schema_id) REFERENCES hr_regulation_pilot.evaluation_result_schema (evaluation_result_schema_id);
    END IF;
END $$;

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'evaluation_schema_source_node_fk' AND conrelid = 'hr_regulation_pilot.evaluation_schema_source'::regclass) THEN
        ALTER TABLE hr_regulation_pilot.evaluation_schema_source ADD CONSTRAINT evaluation_schema_source_node_fk FOREIGN KEY (regulation_node_id) REFERENCES hr_regulation_pilot.regulation_node (regulation_node_id);
    END IF;
END $$;

ALTER TABLE hr_regulation_pilot.evaluation_result_schema ENABLE ROW LEVEL SECURITY;

ALTER TABLE hr_regulation_pilot.evaluation_item_definition ENABLE ROW LEVEL SECURITY;

ALTER TABLE hr_regulation_pilot.evaluation_schema_source ENABLE ROW LEVEL SECURITY;

DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'service_role') THEN
        GRANT USAGE ON SCHEMA hr_regulation_pilot TO service_role;
        GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE
            hr_regulation_pilot.evaluation_result_schema,
            hr_regulation_pilot.evaluation_item_definition,
            hr_regulation_pilot.evaluation_schema_source TO service_role;
    END IF;
END $$;
