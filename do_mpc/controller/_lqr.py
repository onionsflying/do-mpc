
#
#   This file is part of do-mpc
#
#   do-mpc: An environment for the easy, modular and efficient implementation of
#        robust nonlinear model predictive control
#
#   Copyright (c) 2014-2019 Sergio Lucia, Alexandru Tatulea-Codrean
#                        TU Dortmund. All rights reserved
#
#   do-mpc is free software: you can redistribute it and/or modify
#   it under the terms of the GNU Lesser General Public License as
#   published by the Free Software Foundation, either version 3
#   of the License, or (at your option) any later version.
#
#   do-mpc is distributed in the hope that it will be useful,
#   but WITHOUT ANY WARRANTY; without even the implied warranty of
#   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#   GNU Lesser General Public License for more details.
#
#   You should have received a copy of the GNU General Public License
#   along with do-mpc.  If not, see <http://www.gnu.org/licenses/>.

import casadi
from casadi.tools import *
import numpy as np
import warnings
import pdb

import do_mpc.data
from scipy.linalg import solve_discrete_are
from ..model import LinearModel, IteratedVariables

class LQR(IteratedVariables):
    """Linear Quadratic Regulator.
    Use this class to configure and run the LQR controller
    according to the previously configured :py:class:`do_mpc.model.Model` instance. 
    
    Two types of LQR can be desgined:

        1. **Finite Horizon** LQR by choosing, e.g. ``n_horizon = 20``.

        2. **Infinite Horizon** LQR by choosing ``n_horizon = None``.

    The value for ``n_horizon`` is set using :py:meth:`set_param`. 
    
    **Configuration and setup:**
    
    Configuring and setting up the LQR controller involves the following steps:
    
    1. Use :py:meth:`set_param` to configure the :py:class:`LQR` instance.
    
    2. Set the objective of the control problem with :py:meth:`set_objective`
    
    3. To finalize the class configuration call :py:meth:`setup`.
    
    The :py:class:`LQR` can be used in **two different modes**:

    1. **set-point tracking** mode: 

        - Choose set-point (default is ``0``).
        
        - Set ``Q`` and ``delR`` values with :py:meth:`set_objective`.

        - Reformulate objective with :py:meth:`input_rate_penalization` to penalize the input rate.
    
    2. **regulation** mode:

        - Default set-point (``0``) is used.

        - Set ``Q`` and ``R`` values with :py:meth:`set_objective`.
    
    .. warning::
        
        The :py:class:`LQR` cannot use the :py:meth:`input_rate_penalization` mode if the model is converted from an DAE to an ODE system.
        In this case the converted model is already given in the input rate formulation. 
    
    .. note::
        During runtime call :py:meth:`make_step` with the current state :math:`x` to obtain the optimal control input :math:`u`.
        During runtime call :py:meth:`set_setpoint` with the set points of input :math:`u_{ss}` and states :math:`x_{ss}` in order to update the respective set points.
    
    """
    def __init__(self,model):
        self.model = model
        IteratedVariables.__init__(self)
        
        assert isinstance(model, LinearModel), 'LQR can only be used with linear models. Initialize the model with LinearModel class.'
        assert model.flags['setup'] == True, 'Model for LQR was not setup. After the complete model creation call model.setup().'
        assert model.model_type == 'discrete', 'Initialize LQR with discrete system. Discretize the system using LinearModel.discretize()'
        
        self.model_type = model.model_type

        self.data = do_mpc.data.Data(model)
        
        #Parameters necessary for setting up LQR
        self.data_fields = [
            'n_horizon',
            'mode',
            't_step',
            ]
        #Initialize prediction horizon for the problem
        self.n_horizon = None
        
        #Initialize mode of LQR
        self.mode = 'setPointTrack'
        
        self.flags = {'setup':False}

        
        self.xss = None
        self.uss = None

    def reset_history(self):
        """Reset the history of the LQR.
        """
        self._t0 = np.array([0])
        self.data.init_storage()
        
    def discrete_gain(self,A,B):
        """Computes discrete gain. 
        
        This method computes either the finite horizon discrete gain and infinite horizon discrete gain.
        The gain is computed using the solution of the discrete-time algebraic Ricatti equation.
        
        For finite horizon :py:class:`LQR`, the problem formulation is as follows:
            
            .. math::
                \\pi(N) &= P_f\\\\
                K(k) & = -(B'\\pi(k+1)B)^{-1}B'\\pi(k+1)A\\\\
                \\pi(k) & = Q+A'\\pi(k+1)A-A'\\pi(k+1)B(B'\\pi(k+1)B+R)^{-1}B'\\pi(k+1)A
       
        For infinite horizon :py:class:`LQR`, the problem formulation is as follows:
            
            .. math::
                K & = -(B'PB+P)^{-1}B'PA\\\\
                P & = Q+A'PA-A'PB(R+B'PB)^{-1}B'PA\\\\
        
        For example:
            
            ::
                
                K = lqr.discrete_gain(A,B)
                                  
        :param A: State matrix - constant matrix with no variables
        :type A: numpy.ndarray

        :param B: Input matrix - constant matrix with no variables
        :type B: numpy.ndarray                 
        
        :return: Gain matrix :math:`K`
        :rtype: numpy.ndarray

        """
        #Verifying the availability of cost matrices
        assert self.Q.size != 0 and self.R.size != 0 , 'Enter tuning parameter Q and R for the lqr problem using set_objective() function.'
        assert self.model_type == 'discrete', 'convert the model from continous to discrete using model_type_conversion() function.'
        
        #calculating finite horizon gain
        if self.n_horizon !=None:
            assert self.P.size != 0, 'Terminal cost is required to calculate gain. Enter the required value using set_objective() function.'
            temp_p = self.P
            for k in range(self.n_horizon):
                 K = -np.linalg.inv(np.transpose(B)@temp_p@B+self.R)@np.transpose(B)@temp_p@A
                 temp_pi = self.Q+np.transpose(A)@temp_p@A-np.transpose(A)@temp_p@B@np.linalg.inv(np.transpose(B)@temp_p@B+self.R)@np.transpose(B)@temp_p@A
                 temp_p = temp_pi
            return K
        
        #Calculating infinite horizon gain
        elif self.n_horizon == None:
            pi_discrete = solve_discrete_are(A,B, self.Q, self.R)
            K = -np.linalg.inv(np.transpose(B)@pi_discrete@B+self.R)@np.transpose(B)@pi_discrete@A
            return K
    
    def input_rate_penalization(self,A,B):
        """Compute the :py:class:`LQR` gain after reformulating the problem to consider an input-rate penalization term. 
        
        .. warning::

            Calling :py:meth:`input_rate_penalization`  modifies the objective function
            as well as the state and input matrix.
        
        .. warning::
            
            LQR cannot be made to execute in the input rate penalization mode if the model is converted from DAE to ODE system.
            Because the converted model itself is in input rate penalization mode.

        The input rate penalization formulation is given as:
            
            .. math::
                x(k+1) = \\tilde{A} x(k) + \\tilde{B}\\Delta u(k)\\\\
                
                \\text{where} \\quad
                \\tilde{A} = \\begin{bmatrix} 
                                A & B \\\\
                                0 & I \\end{bmatrix},
                \\tilde{B} = \\begin{bmatrix} B \\\\
                             I \\end{bmatrix}
                            
        We introduce as new states of this system :math:`\\tilde{x} = [x,u]` 
        where :math:`x` and :math:`u` are the original states and input of the system.
        After reformulating the system with :py:meth:`input_rate_penalization`, the discrete gain is calculated
        using :py:meth:`discrete_gain`.
        
        As the system state matrix and input matrix are altered,
        cost matrices are also modified accordingly:
            
            .. math::
                \\tilde{Q} = \\begin{bmatrix}
                                Q & 0 \\\\
                                0 & R \\end{bmatrix},
                \\tilde{R} = \\Delta R
                
            where :math:`\\Delta R` is passed as additional argument to the :py:meth:`set_objective` while setting the mode of operation to ``inputRatePenalization``.
        
        :return: Gain matrix :math:`K`
        :rtype: numpy.ndarray
        

        """
        #Verifying input cost matrix for input rate penalization
        assert self.Rdelu.size != 0 , 'set R_delu parameter using set_param() fun.'
        
        #Modifying A and B matrix for input rate penalization
        identity_u = np.identity(np.shape(B)[1])
        zeros_A = np.zeros((np.shape(B)[1],np.shape(A)[1]))
        self.A_new = np.block([[A,B],[zeros_A,identity_u]])
        self.B_new = np.block([[B],[identity_u]])
        zeros_Q = np.zeros((np.shape(self.Q)[0],np.shape(self.R)[1]))
        zeros_Ru = np.zeros((np.shape(self.R)[0],np.shape(self.Q)[1]))
        
        #Modifying Q and R matrix for input rate penalization
        self.Q = np.block([[self.Q,zeros_Q],[zeros_Ru,self.R]])
        if self.n_horizon != None:
            self.P = np.block([[self.P,zeros_Q],[zeros_Ru,self.R]])
        self.R = self.Rdelu
        
        #Computing gain matrix
        K = self.discrete_gain(self.A_new, self.B_new)
        return K
 
    
    def set_param(self,**kwargs):
        """Set the parameters of the :py:class:`LQR` class. Parameters must be passed as pairs of valid keywords and respective argument.
        
        Two different kinds of LQR can be desgined. In order to design a finite horizon LQR, ``n_horizon`` and to design a infinite horizon LQR, ``n_horizon`` 
        should be set to ``None``(default value).
        
        For example:

        ::

            lqr.set_param(n_horizon = 20)

        It is also possible and convenient to pass a dictionary with multiple parameters simultaneously as shown in the following example:

        ::

            setup_lqr = {
                'n_horizon': 20,
                't_sample': 0.5,
            }
            lqr.set_param(**setup_mpc)
        
        This makes use of thy python "unpack" operator. See `more details here`_.

        .. _`more details here`: https://codeyarns.github.io/tech/2012-04-25-unpack-operator-in-python.html

        .. note:: The only required parameters  are ``n_horizon``. All other parameters are optional.

        .. note:: :py:meth:`set_param` can be called multiple times. Previously passed arguments are overwritten by successive calls.

        The following parameters are available:
            
        :param n_horizon: Prediction horizon of the optimal control problem. Parameter must be set by user.
        :type n_horizon: int

        :param t_sample: Sampling time for converting continuous time system to discrete time system.
        :type t_sample: float
        
        :param mode: mode for operating LQR
        :type mode: String
        
        :param conv_method: Method for converting continuous time to discrete time system
        :type conv_method: String
        
        """
        for key, value in kwargs.items():
            if not (key in self.data_fields):
                print('Warning: Key {} does not exist for LQR.'.format(key))
            else:
                setattr(self, key, value)
                
    def make_step(self,x0):
        """Main method of the class during runtime. This method is called at each timestep
        and returns the control input for the current initial state.
            

        :param x0: Current state of the system.
        :type x0: numpy.ndarray

        :return: u0
        :rtype: numpy.ndarray
        """
        #verify setup of lqr is done
        assert self.flags['setup'] == True, 'LQR is not setup. run setup() function.'
        
        # Check input type.
        if isinstance(x0, (np.ndarray, casadi.DM)):
            pass
        elif isinstance(x0, structure3.DMStruct):
            x0 = x0.cat
        else:
            raise Exception('Invalid type {} for x0. Must be {}'.format(type(x0), (np.ndarray, casadi.DM, structure3.DMStruct)))

        #setting setpoints
        if self.xss is None and self.uss is None:
            self.set_setpoint()
        
        
        #Calculate u in set point tracking mode
        if self.mode == "setPointTrack":
            if self.xss.size != 0 and self.uss.size != 0:
                u0 = self.K@(x0-self.xss)+self.uss
        
        #Calculate u in input rate penalization mode
        elif self.mode == "inputRatePenalization":
            if np.shape(self.K)[1]==np.shape(x0)[0]:
                u0 = self.K@(x0-self.xss)+self.uss
                u0 = self.u0+x0[-self.model.n_u:]
            elif np.shape(self.K)[1]!=np.shape(x0)[0] and np.shape(self.K)[1]== np.shape(np.block([[x0],[self.u0]]))[0]:
                x0_new = np.block([[x0],[self.u0]])
                u0 = self.K@(x0_new-self.xss)+self.uss
                u0 = self.u0+x0_new[-self.model.n_u:]


        t0 = self._t0
        # Store solution:
        self.data.update(_x = x0)
        self.data.update(_u = u0)
        self.data.update(_time = t0)

        # Update initial
        self._t0 = self._t0 + self.t_step
        self._x0.master = x0
        self._u0.master = u0
        
        return self.u0
        
    def set_objective(self, Q = None, R = None, P = None, Rdelu = None):
        """Sets the cost matrix for the Optimal Control Problem.
        
        This method sets the inputs, states and algebraic states cost matrices for the given problem.
        
        .. note::
            For the problem to be solved in input rate penalization mode, ``Q``, ``R`` and ``Rdelu`` should be set.
            
        For example:
            
            ::
                
                # Values used are to show how to use this function.
                # For ODE models
                lqr.set_objective(Q = np.identity(2), R = np.identity(2))
                
                # For ODE models with input rate penalization
                lqr.set_objective(Q = np.identity(2), R = 5*np.identity(2), Rdelu = np.identity(2))
                
        
        :param Q: State cost matrix
        :type Q: numpy.ndarray
        
        :param R: Input cost matrix
        :type R: numpy.ndarray
        
        :param Rdelu: Input rate cost matrix
        :type Rdelu: numpy.ndarray
        
        :raises exception: Please set input cost matrix for input rate penalization/daemodel using :py:meth:`set_objective`.
        :raises exception: Q matrix must be of type class numpy.ndarray
        :raises exception: R matrix must be of type class numpy.ndarray
        :raises exception: P matrix must be of type class numpy.ndarray

        .. warning::
            ``Q``, ``R``, ``P`` is chosen as matrix of zeros since it is not passed explicitly.
            If ``P`` is not given explicitly, then ``Q`` is chosen as ``P`` for calculating finite discrete gain

        """
        
        #Verify the setup is not complete
        assert self.flags['setup'] == False, 'Objective can not be set after LQR is setup'
        
        #Set Q, R, P
        if Q is None:
            self.Q = np.zeros((self.model.n_x,self.model.n_x))
            warnings.warn('Q is chosen as matrix of zeros since Q is not passed explicitly.')
        else:
            self.Q = Q
        if R is None:
            self.R = np.zeros((self.model.n_u,self.model.n_u))
            warnings.warn('R is chosen as matrix of zeros.')
        else:
            self.R = R   
        if P is None and self.n_horizon != None:
            self.P = Q
            warnings.warn('P is not given explicitly. Q is chosen as P for calculating finite discrete gain')
        else:
            self.P = P

        #Set delRu for input rate penalization or converted ode model
        if (self.mode == 'inputRatePenalization') and np.all(Rdelu != None):
            self.Rdelu = Rdelu
        elif (self.mode == 'inputRatePenalization') and np.all(Rdelu == None):
            raise Exception('Please set input cost matrix for input rate penalization/daemodel using set_objective()')
        
        #Verify shape of Q,R,P
        assert self.Q.shape == (self.model.n_x,self.model.n_x), 'Q must have shape = {}. You have {}'.format((self.model.n_x,self.model.n_x),self.Q.shape)
        assert self.R.shape == (self.model.n_u,self.model.n_u), 'R must have shape = {}. You have {}'.format((self.model.n_u,self.model.n_u),self.R.shape)
        if isinstance(self.Q, (casadi.DM, casadi.SX, casadi.MX)):
            raise Exception('Q matrix must be of type class numpy.ndarray')
        if isinstance(self.R, (casadi.DM, casadi.SX, casadi.MX)):
            raise Exception('R matrix must be of type class numpy.ndarray')
        if self.n_horizon != None and isinstance(self.P, (casadi.DM, casadi.SX, casadi.MX)):
            raise Exception('P matrix must be of type class numpy.ndarray')
        if self.n_horizon != None:
            assert self.P.shape == self.Q.shape, 'P must have same shape as Q. You have {}'.format(P.shape)

    def set_setpoint(self,xss = None,uss = None):   
        """Sets setpoints for states and inputs.
        
        This method can be used to set setpoints at each time step. It can be called inside simulation loop to change the set point dynamically.
        
        .. note::
            If setpoints is not specifically mentioned it will be set to zero (default).
        
        For example:
            
            ::
                
                # For ODE models
                lqr.set_setpoint(xss = np.array([[10],[15]]) ,uss = np.array([[2],[3]]))

        :param xss: set point for states of the system(optional)
        :type xss: numpy.ndarray
        
        :param uss: set point for input of the system(optional)
        :type uss: numpy.ndarray

        """
        assert self.flags['setup'] == True, 'LQR is not setup. Run setup() function.'
        if xss is None:
            self.xss = np.zeros((self.model.n_x,1))
        else:
            self.xss = xss
        
        if uss is None:
            self.uss = np.zeros((self.model.n_u,1))
        else:
            self.uss = uss
        if self.mode == 'inputRatePenalization':
            self.xss = np.block([[self.xss],[self.uss]])
            self.uss = np.zeros((self.model.n_u,1))
            assert self.xss.shape == (self.model.n_x+self.model.n_u,1), 'xss must be of shape {}. You have {}'.format((self.model.n_x+self.model.n_u,1),self.xss.shape)
        
        if self.mode == 'setPointTrack':
            assert self.xss.shape == (self.model.n_x,1), 'xss must be of shape {}. You have {}'.format((self.model.n_x,1),self.xss.shape)
        assert self.uss.shape == (self.model.n_u,1), 'uss must be of shape {}. You have {}'.format((self.model.n_u,1),self.uss.shape)
    
    def setup(self):
        """Prepares lqr for execution.
        This method initializes and make sure that all the necessary parameters required to run the lqr are available.
        
        :raises exception: mode must be setPointTrack, inputRatePenalization, None. you have {string value}

        """
        assert self.t_step, 't_step is required in order to setup the LQR. Please set the simulation time step via set_param(**kwargs)'

        if self.mode in ['setPointTrack',None]:
            self.K = self.discrete_gain(self.model._A,self.model._B)
        elif self.mode == 'inputRatePenalization':
            self.K = self.input_rate_penalization(self.model._A,self.model._B)
        if not self.mode in ['setPointTrack','inputRatePenalization']:
            raise Exception('mode must be setPointTrack, inputRatePenalization, None. you have {}'.format(self.method))
        self.flags['setup'] = True