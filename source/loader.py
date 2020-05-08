import os, copy, pickle
import pandas as pd
import numpy as np
import subprocess as sp
import configparser
from sklearn.gaussian_process import GaussianProcessRegressor as GPR
from sklearn.gaussian_process.kernels import RBF, ConstantKernel as C
from fitting import FittingModel


class Learner():
    def __init__(self, settings_file="settings.ini"):
        self.settings = settings_file
        self.load()
        self.prepare()

    def load(self):
        config = configparser.ConfigParser()
        config.read(self.settings)
        self.main = os.getcwd()
        self.file_train = os.path.join([self.main, config.get("locations", "train_set")])
        self.file_train = os.path.join([self.main, config.get("locations", "test_set")])
        self.desc_file = os.path.join([self.main, config.get("locations", "desc_file")])
        self.fit_fold = [self.main, 'fitting']
        self.fit_code = config.get("locations", 'fitting_code')
        self.fit_exe = config.get("locations", "fit_exe")
        self.eval_exe = config.get("locations", "eval_exe")
        self.calculations = os.path.join([self.main, "calculations"])
        self.output = os.path.join([self.main, "output"])

        self.username = config.get("account", "username")
        self.email = config.get("account", "email")

        self.E_min = config.getfloat("fitting", "E_min")
        self.delta_E = config.getfloat("fitting", "delta_E")
        self.nfits = config.getint("fitting", "nfits")

        self.t = config.getint("iterations", "t")
        self.tmax = config.getint("iterations", "tmax")
        self.batch = config.getint("iterations", "batch")
        self.first_batch = config.getint("iterations", "first_batch")
        self.restart = config.getboolean("iterations", "restart")
        if self.restart:
            self.restart_file = config.get("iterations", "restart_file")

        self.cluster_sz = config.getint("active learning", "cluster_sz")
        self.STD_w = config.getfloat("active learning", "STD_w")
        self.TRAINERR_w = config.getfloat("active learning", "TRAINERR_w")

    def read_data(self, xyz_path, picked_idx=[], energies=False):
        command = F'head -n 2 {xyz_path} | tail -n 1'
        call = sp.Popen(command, shell=True, stdout=sp.PIPE)
        E_columns = len(call.communicate()[0].split())

        coords = pd.read_csv(xyz_path, sep='\s+', names=range(max(E_columns, 4)))
        natoms = int(coords.iloc[0][0])
        xyz = coords.iloc[coords.index % (natoms + 2) > 1, :].copy()

        at_list = xyz.loc[:natoms + 1, 0:0].values
        at_list = at_list.reshape(natoms)
        xyz = xyz.loc[:, 1:4].values
        xyz = xyz.reshape((-1, natoms, 3))

        if (len(picked_idx) > 0):
            xyz = xyz[picked_idx, :]

        if energies:
            E = coords.iloc[coords.index % (natoms+2) == 1, :].copy()
            E = E.iloc[:, :E_columns].values
            E = E.astype(np.float)
            if (len(picked_idx) > 0):
                E = E[picked_idx, :]
            return at_list, xyz, E
        else:
            return at_list, xyz

    def write_energy_file(self, infile, outfile='tofitE.dat', picked_idx=[], col_to_write=0):
        max_col_idx = 0
        if type(col_to_write) is int:
            max_col_idx = col_to_write + 1
        elif type(col_to_write) is list:
            try:
                max_col_idx = max(col_to_write)
            except:
                print('Error !!!')
                return False

        _, e = self.read_data(infile, picked_idx=picked_idx, E_columns=max_col_idx)

        np.savetxt(outfile,  e[:, col_to_write], delimiter='    ')

        return True

    def get_weights(self, E,  E_min=None):
        if E_min is None:
            E_min = np.min(E)
        w = np.square(self.delta_E/(E - E_min + self.delta_E))
        w_mean = np.mean(w)
        w /= w_mean

        return w, E_min

    def generate_set(self, infile=None, outfile='set.xyz', picked_idx=[]):
        try:
            data = pd.read_csv(infile, sep='\s+', names=range(4))
            Natom = int(data.iloc[0][0])
            with open(outfile, 'w') as f:
                for i in picked_idx:
                    line_start = i * (Natom + 2)
                    try:
                        print('{0:s} '.format(data.iloc[line_start, 0]), end='\n', file=f)  # number of atoms in one configuration
                        for d in data.iloc[line_start+1, :]:
                            try:
                                if np.isnan(d):
                                    continue
                                else:
                                    print(' {0:s} '.format(str(d)), end='\t', file=f)  # energies
                            except:
                                print(' {0:s} '.format(str(d)), end='\t', file=f)  # energies
                        print(' ', end='\n', file=f)
                        for a in range(Natom):
                            for d in data.iloc[line_start+2+a, :]:
                                print(' {0:s} '.format(str(d)), end='\t', file=f)
                            print('', end='\n', file=f)
                    except:
                        continue
            return True
        except Exception as e:
            print('create new trainset file fails: ' + str(e))
        return None

    def prepare(self):
        self.kernel = C(1.0, (1e-5, 1e5)) * RBF(15, (1e-5, 1e5))
        self.gp = GPR(kernel=self.kernel, n_restarts_optimizer=9, alpha=1e-6)
        self.model = FittingModel(self.main, self.fit_exe, self.eval_exe)

        _, self.coords = self.read_data(self.file_train)
        with open(self.desc_file, 'rb') as pickled:
            self.X_train = pickle.load(pickled)
        self.Y_train = np.zeros(self.X_train.shape[0])

        self.idx_all = np.arange(self.X_train.shape[0])
        self.idx_left = copy.deepcopy(self.idx_all)
        self.idx_now = self.idx_all[~np.in1d(self.idx_all, self.idx_left)]
        # idx_failed = np.ones(idx_all.shape[0], dtype=bool)
        self.err_train = np.zeros_like(self.idx_all, dtype=float)

        os.makedirs(self.output, exist_ok=True)

        self.write_energy_file(self.file_test, self.output + 'val_refer.dat', col_to_write=1)
        self.model.init(self.output, self.file_test, self.E_min)
        self.file_train_tmp = '_trainset_tmp.xyz'

        # Logfile
        self.logfile = self.output + '_PickSet.log'
        to_log = ['Iteration' 'TrainSet_Size', 'Leftover_Size',
                  'Train_MSE', 'Train_wMSE', 'Test_MSE', 'Test_wMSE',
                  'Fitting[s]',
                  ]
        with open(self.logfile, 'w') as f:
            for key in to_log:
                print(key, end='\t', file=f)
            print('', end='\n', file=f)

        # saving settings
        to_record = {
            'Nr of samples in first iteration': self.first_batch,
            'Nr of samples picked in other iterations': self.batch,
            'cluster size': self.cluster_sz,
            'STD weight': self.STD_w,
            'TRAIN ERROR weight': self.TRAINERR_w,
            'Used Guassian Process model': self.gp,
        }

        with open(self.output + '_setting.ini', 'w') as f:
            for key, value in to_record.items():
                try:
                    print(key + '\t' + str(value), end='\n', file=f)
                except:
                    print(key, end='\n', file=f)
                    print(value, end='\n', file=f)
