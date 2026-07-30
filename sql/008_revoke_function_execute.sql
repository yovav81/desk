-- Applied in prod 2026-07-28, Phase 15E — revoke EXECUTE on SECURITY DEFINER funcs from anon+authenticated

revoke execute on function
  public.can_see_emails(),
  public.handle_new_user(),
  public.is_admin(),
  public.is_approved(),
  public.rls_auto_enable()
from anon;

revoke execute on function
  public.can_see_emails(),
  public.handle_new_user(),
  public.is_admin(),
  public.is_approved(),
  public.rls_auto_enable()
from authenticated;

