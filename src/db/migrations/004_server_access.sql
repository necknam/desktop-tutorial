-- Server-side service-role access; no anonymous read/write policies are introduced.

ALTER TABLE public.roadmap_sessions ENABLE ROW LEVEL SECURITY;

ALTER TABLE public.project_plans ENABLE ROW LEVEL SECURITY;

ALTER TABLE public.plan_conversations ENABLE ROW LEVEL SECURITY;

ALTER TABLE public.assessment_questions ENABLE ROW LEVEL SECURITY;

ALTER TABLE public.assessment_responses ENABLE ROW LEVEL SECURITY;

ALTER TABLE public.roadmap_generations ENABLE ROW LEVEL SECURITY;

ALTER TABLE public.roadmap_selections ENABLE ROW LEVEL SECURITY;

ALTER TABLE public.roadmap_reports ENABLE ROW LEVEL SECURITY;

ALTER TABLE public.assessment_reports ENABLE ROW LEVEL SECURITY;

ALTER TABLE hr_regulation_pilot.institution ENABLE ROW LEVEL SECURITY;

ALTER TABLE hr_regulation_pilot.regulation ENABLE ROW LEVEL SECURITY;

ALTER TABLE hr_regulation_pilot.regulation_version ENABLE ROW LEVEL SECURITY;

ALTER TABLE hr_regulation_pilot.regulation_file ENABLE ROW LEVEL SECURITY;

ALTER TABLE hr_regulation_pilot.regulation_node ENABLE ROW LEVEL SECURITY;

ALTER TABLE hr_regulation_pilot.regulation_scope ENABLE ROW LEVEL SECURITY;

ALTER TABLE hr_regulation_pilot.regulation_reference ENABLE ROW LEVEL SECURITY;

ALTER TABLE hr_regulation_pilot.rag_chunk ENABLE ROW LEVEL SECURITY;

ALTER TABLE hr_regulation_pilot.rag_chunk_source ENABLE ROW LEVEL SECURITY;

DO $access$ BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'service_role') THEN
    GRANT USAGE ON SCHEMA public, hr_regulation_pilot TO service_role;
    GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public, hr_regulation_pilot TO service_role;
    GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public, hr_regulation_pilot TO service_role;
  END IF;
END $access$;
