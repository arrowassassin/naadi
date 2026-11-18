-- review19 R4: read-only pre-deploy check for migration 0036 (run on the hosted DB BEFORE deploying 0036).
begin transaction read only;

-- 1. Placements that 0036 will leave as 'admin' (never deleted by the seed) although they use a seed figure:
--    an admin attached a seed figure to another section, or edited the section. Informational; any row here is
--    exactly what the old back-fill would have mislabelled 'seed' and a later seed run would have deleted.
select sm.section_id, sm.media_id, m.url, s.admin_edited_at,
       jsonb_path_exists(s.body, '$.** ? (@.type == "figure" && @.attrs.mediaId == $m)',
                         jsonb_build_object('m', m.id)) as figure_node_in_body
  from public.section_media sm
  join public.media_assets m on m.id = sm.media_id
  join public.chapter_sections s on s.id = sm.section_id
 where m.kind = 'svg' and m.url ~ '^[a-z0-9-]+/figures/[^/]+$'
   and not (m.url = split_part(m.url, '/', 1) || '/figures/' || m.id || '.svg'
            and s.admin_edited_at is null
            and jsonb_path_exists(s.body, '$.** ? (@.type == "figure" && @.attrs.mediaId == $m)',
                                  jsonb_build_object('m', m.id)))
 order by 1, 2;

-- 2. Totals: how 0036 will label every existing placement (will_be_seed should equal the seed's figure placements,
--    95 for the current bundle, unless admins edited seeded sections).
select count(*) as placements,
       count(*) filter (where m.kind = 'svg'
                          and m.url = split_part(m.url, '/', 1) || '/figures/' || m.id || '.svg'
                          and s.admin_edited_at is null
                          and jsonb_path_exists(s.body, '$.** ? (@.type == "figure" && @.attrs.mediaId == $m)',
                                                jsonb_build_object('m', m.id))) as will_be_seed
  from public.section_media sm
  join public.media_assets m on m.id = sm.media_id
  join public.chapter_sections s on s.id = sm.section_id;

rollback;
