import unittest
import pandas as pd
import extended_style_context_builder as mod

class ExtendedStyleContextTests(unittest.TestCase):
    def test_prior_profile_does_not_use_current_match(self):
        rows=[]
        for i in range(6):
            rows.append({"season":2024,"date":pd.Timestamp("2024-08-01")+pd.Timedelta(days=i),"team":"A","opponent":"B","is_home":1,"poss_for":50+i,"shots_for":10+i,"shots_against":8,"sot_for":4,"sot_against":3,"shots_1h_for":5,"shots_2h_for":5+i,"sot_1h_for":2,"sot_2h_for":2,"corners_for":5,"corners_against":4,"yellow_for":2})
        base=pd.DataFrame(rows)
        a=mod.add_prior_profiles(base)
        changed=base.copy(); changed.loc[5,"shots_for"]=999
        b=mod.add_prior_profiles(changed)
        self.assertEqual(a.loc[5,"prior_shots_for"],b.loc[5,"prior_shots_for"])

if __name__ == "__main__":
    unittest.main()
