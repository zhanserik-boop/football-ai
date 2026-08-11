import unittest
import numpy as np
import pandas as pd
import market_anchored_fair_ah_v2 as mod

class FairAHV2Tests(unittest.TestCase):
    def _frame(self):
        rows=[]
        for season in (2022,2023,2024):
            for i in range(320):
                row={"season":season,"date":pd.Timestamp(f"{season}-08-01")+pd.Timedelta(days=i),"home_team":f"H{i%20}","away_team":f"A{i%20}","open_ah_home_line":0.25,"close_move_home":0.01*(i%5)}
                for f in mod.FEATURES_V2: row[f]=0.1+(i%7)*0.01
                rows.append(row)
        return pd.DataFrame(rows)

    def test_walk_forward_uses_prior_seasons_only(self):
        out=mod.walk_forward_features(self._frame(),mod.FEATURES_V2)
        self.assertTrue((out[out.season==2023]["model_train_through_season_v2"]==2022).all())
        self.assertTrue((out[out.season==2024]["model_train_through_season_v2"]==2023).all())

    def test_future_season_change_does_not_change_prior_prediction(self):
        a=self._frame(); b=a.copy(); b.loc[b.season==2024,"close_move_home"]+=5
        pa=mod.walk_forward_features(a,mod.FEATURES_V2)
        pb=mod.walk_forward_features(b,mod.FEATURES_V2)
        np.testing.assert_allclose(pa.loc[pa.season==2023,"predicted_close_move_home_v2"],pb.loc[pb.season==2023,"predicted_close_move_home_v2"],equal_nan=True)

if __name__ == "__main__": unittest.main()
